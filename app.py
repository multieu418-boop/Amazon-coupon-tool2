import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 报错修复工具", layout="wide")

# --- 1. 智能解析报错信息 ---
def parse_error_details(error_msg):
    error_map = {}
    if pd.isna(error_msg) or str(error_msg).strip() == "": 
        return error_map
    
    # 正则提取：10位ASIN + 报错内容
    blocks = re.split(r'([A-Z0-9]{10})\n', str(error_msg))
    for i in range(1, len(blocks), 2):
        asin = blocks[i].strip()
        content = blocks[i+1]
        # 寻找“要求的净价格”
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
        req_price = float(req_price_match.group(1)) if req_price_match else None
        is_no_ref = "没有经验证" in content
        error_map[asin] = {"req_price": req_price, "is_no_ref": is_no_ref, "msg": content.strip()}
    return error_map

# --- 2. 智能读取亚马逊报错模板 (跳过说明行) ---
def load_amazon_error_file(file):
    if file is None: return None
    try:
        # 读取前 20 行来定位表头
        df_scan = pd.read_excel(file, header=None, nrows=20)
        header_idx = 0
        for i, row in df_scan.iterrows():
            row_content = "".join([str(x) for x in row.values]).lower()
            # 只要包含这几个核心词，就认为是表头行
            if 'asin' in row_content or 'discount' in row_content or '折扣' in row_content:
                header_idx = i
                break
        
        file.seek(0)
        df = pd.read_excel(file, header=header_idx)
        df.columns = [str(c).strip().lower() for c in df.columns]
        # 过滤掉全空行
        df = df.dropna(how='all').reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"解析报错模板失败: {e}")
        return None

# --- 3. 智能读取 All Listing (支持多格式/多编码) ---
def load_listing_file(file):
    if file is None: return None
    fname = file.name.lower()
    try:
        df = None
        if fname.endswith('.txt'):
            for enc in ['utf-8', 'utf-16', 'gbk', 'utf-8-sig']:
                try:
                    file.seek(0)
                    df = pd.read_csv(file, sep='\t', encoding=enc)
                    if not df.empty: break
                except: continue
        elif fname.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        if df is not None:
            df.columns = [str(c).strip().lower() for c in df.columns]
            return df
    except Exception as e:
        st.error(f"读取 Listing 失败: {e}")
        return None

def main():
    st.title("🎯 Amazon Coupon 智能修复重组")
    st.markdown("---")

    col_l, col_r = st.columns(2)
    with col_l:
        l_file = st.file_uploader("1. 上传 All Listing 表", type=['txt', 'xlsx', 'csv'])
    with col_r:
        e_file = st.file_uploader("2. 上传亚马逊报错的 Excel 模板", type=['xlsx'])

    if l_file and e_file:
        df_l = load_listing_file(l_file)
        df_e = load_amazon_error_file(e_file)

        if df_l is not None and df_e is not None:
            # 定位 Listing 的 ASIN 和 Price 列
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)

            # 定位 报错模板 的 ASIN List 和 Error 列
            e_asin_col = next((c for c in df_e.columns if 'asin' in c and 'list' in c or 'asin列表' in c), None)
            e_error_col = next((c for c in df_e.columns if 'error' in c or '处理结果' in c or '批注' in c or 'summary' in c), df_e.columns[-1])

            # 抓取原始信息用于重组
            budget_col = next((c for c in df_e.columns if 'budget' in c or '预算' in c), None)
            discount_col = next((c for c in df_e.columns if 'discount' in c or '折扣' in c), None)
            start_col = next((c for c in df_e.columns if 'start' in c or '开始' in c), None)
            end_col = next((c for c in df_e.columns if 'end' in c or '结束' in c), None)

            # --- 拆解 ASIN ---
            all_items = []
            for idx, row in df_e.iterrows():
                asin_str = str(row.get(e_asin_col, ""))
                if asin_str == "nan" or not asin_str: continue
                
                error_map = parse_error_details(str(row.get(e_error_col, "")))
                asins = [a.strip() for a in asin_str.replace(',', ';').split(';') if a.strip()]
                
                for a in asins:
                    # 匹配原价
                    price_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    price = price_match[0] if len(price_match) > 0 else None
                    
                    is_err = a in error_map
                    all_items.append({
                        "原始行": idx + 1,
                        "ASIN": a,
                        "状态": "❌ 报错" if is_err else "✅ 正常",
                        "原价": price,
                        "要求净价": error_map[a]['req_price'] if is_err else None,
                        "建议折扣": 0, # 待计算
                        "报错原因": error_map[a]['msg'] if is_err else "正常",
                        "原始折扣": row.get(discount_col, 5),
                        "meta": row.to_dict()
                    })

            df_work = pd.DataFrame(all_items)

            # 计算建议折扣
            def calc_logic(r):
                if r['状态'] == "✅ 正常": return r['原始折扣']
                if "没有经验证" in r['报错原因']: return 0
                if pd.notnull(r['原价']) and pd.notnull(r['要求净价']):
                    needed = math.ceil(((r['原价'] - r['要求净价']) / r['原价']) * 100)
                    return max(needed, 5)
                return 0

            df_work['建议折扣'] = df_work.apply(calc_logic, axis=1)
            df_work['保留'] = df_work['建议折扣'] > 0

            # --- 展示界面 ---
            st.subheader("🛠️ ASIN 修复决策工作台")
            edited_df = st.data_editor(
                df_work[['保留', 'ASIN', '状态', '建议折扣', '原价', '要求净价', '报错原因', '原始行']],
                column_config={
                    "建议折扣": st.column_config.NumberColumn("拟用折扣%", format="%d%%"),
                    "保留": st.column_config.CheckboxColumn("确认保留?", default=True)
                },
                disabled=['ASIN', '状态', '原价', '要求净价', '报错原因', '原始行'],
                hide_index=True
            )

            # --- 归纳导出 ---
            if st.button("🚀 生成并下载修复后的提报文件"):
                final = edited_df[edited_df['保留'] == True]
                if final.empty:
                    st.warning("请勾选要保留的 ASIN")
                else:
                    output_data = []
                    # 按 (原始行号 + 建议折扣) 归纳
                    grouped = final.groupby(['原始行', '建议折扣'])
                    for (orig_idx, disc), group in grouped:
                        orig_meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['meta'].iloc[0]
                        
                        output_data.append({
                            "ASIN 列表": ";".join(group['ASIN'].tolist()),
                            "折扣百分比": disc,
                            "每位客户兑换次数限制": "是",
                            "预算": orig_meta.get(budget_col, 1000),
                            "名称": f"Fixed-{orig_meta.get(discount_col, '')}%-To-{disc}%",
                            "开始日期": orig_meta.get(start_col, ""),
                            "结束日期": orig_meta.get(end_col, "")
                        })
                    
                    res_df = pd.DataFrame(output_data)
                    st.dataframe(res_df)
                    
                    excel_out = io.BytesIO()
                    with pd.ExcelWriter(excel_out, engine='openpyxl') as writer:
                        res_df.to_excel(writer, index=False)
                    st.download_button("📥 下载 Excel", excel_out.getvalue(), "Fixed_Coupons.xlsx")

if __name__ == "__main__":
    main()
