import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 修复重组工具", layout="wide")

# --- 1. 智能解析报错详情 ---
def parse_error_details(error_msg):
    error_map = {}
    if pd.isna(error_msg) or str(error_msg).strip() == "": 
        return error_map
    
    # 尝试匹配 ASIN(10位) + 换行 + 报错内容
    blocks = re.split(r'([A-Z0-9]{10})\n', str(error_msg))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            error_map[asin] = {"req_price": req_price, "msg": content.strip()}
    else:
        # 针对单行报错的兜底逻辑
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', str(error_msg))
        req_price = float(req_price_match.group(1)) if req_price_match else None
        error_map["GLOBAL"] = {"req_price": req_price, "msg": str(error_msg)}
    return error_map

# --- 2. 针对性读取函数：跳过说明与举例 ---
def load_amazon_template(file):
    if file is None: return None
    fname = file.name.lower()
    try:
        # 判定文件类型
        is_csv = fname.endswith('.csv') or (fname.endswith('.xlsx') and 'csv' in fname)
        
        if is_csv:
            # CSV 处理：第7行是表头(index=6)，从第10行开始是数据(即跳过表头后的前3行)
            df = pd.read_csv(file, header=6, encoding='utf-8-sig') # 自动识别常见CSV编码
            # 跳过第8-9行（即读取后的前两行索引为0, 1的数据）
            df = df.iloc[2:].reset_index(drop=True)
        else:
            # Excel 处理：第7行是表头(index=6)
            df = pd.read_excel(file, header=6)
            # 跳过举例行
            df = df.iloc[2:].reset_index(drop=True)

        # 统一列名格式
        df.columns = [str(c).strip().lower() for c in df.columns]
        # 彻底过滤掉全空行
        df = df.dropna(how='all').reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"解析模板失败: {e}。请检查文件格式是否正确。")
        return None

# --- 3. Listing 文件读取 ---
def load_listing_file(file):
    if file is None: return None
    try:
        fname = file.name.lower()
        if fname.endswith('.txt'):
            file.seek(0)
            df = pd.read_csv(file, sep='\t', encoding='utf-8')
        elif fname.endswith('.csv'):
            file.seek(0)
            df = pd.read_csv(file)
        else:
            file.seek(0)
            df = pd.read_excel(file)
        df.columns = [str(c).strip().lower() for c in df.columns]
        return df
    except:
        # 编码重试逻辑
        file.seek(0)
        return pd.read_csv(file, sep='\t', encoding='gbk')

def main():
    st.title("🎯 Amazon Coupon 报错精准剥离系统")
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 表", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传亚马逊报错模板 (CSV/XLSX)", type=['xlsx', 'csv'])

    if l_file and e_file:
        df_l = load_listing_file(l_file)
        df_e = load_amazon_template(e_file)

        if df_l is not None and df_e is not None:
            # 列名自动定位
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)
            
            # 报错模板中的列（根据你的描述和截图）
            e_asin_col = next((c for c in df_e.columns if 'asin' in c and ('list' in c or '列表' in c)), df_e.columns[0])
            e_err_col = next((c for c in df_e.columns if any(k in c for k in ['error', '结果', '错误', 'summary'])), df_e.columns[-1])

            # --- 核心逻辑：拆解 ASIN 并归类 ---
            rows = []
            for idx, row in df_e.iterrows():
                asin_str = str(row.get(e_asin_col, ""))
                if not asin_str or asin_str == "nan": continue
                
                err_text = str(row.get(e_err_col, ""))
                err_map = parse_error_details(err_text)
                # 兼容分号、逗号和空格拆分
                asins = [a.strip() for a in re.split(r'[;,\s]+', asin_str) if a.strip()]
                
                for a in asins:
                    p_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    price = p_match[0] if len(p_match) > 0 else None
                    
                    is_err = a in err_map or (len(asins) == 1 and "GLOBAL" in err_map)
                    specific_err = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    rows.append({
                        "原始行": idx + 10, # 对应 Excel 真实行号
                        "ASIN": a,
                        "状态": "❌ 报错" if is_err else "✅ 正常",
                        "原价": price,
                        "要求净价": specific_err.get('req_price'),
                        "报错原因": specific_err.get('msg', "正常"),
                        "原始折扣": row.get('折扣百分比', row.get('优惠券“折扣”数值', 5)),
                        "meta": row.to_dict()
                    })

            df_work = pd.DataFrame(rows)

            # 计算修复折扣
            def calc_repair(r):
                if r['状态'] == "✅ 正常": return r['原始折扣']
                if r['要求净价'] and r['原价']:
                    needed = math.ceil(((r['原价'] - r['要求净价']) / r['原价']) * 100)
                    # 如果计算出的折扣过大（如超过50%），标记为待核实或剔除
                    return max(needed, 5)
                return 0

            df_work['修复折扣'] = df_work.apply(calc_repair, axis=1)
            df_work['保留'] = df_work['修复折扣'] > 0

            st.subheader("🔍 数据处理预览 (已跳过举例行)")
            edited_df = st.data_editor(
                df_work[['保留', 'ASIN', '状态', '修复折扣', '原价', '要求净价', '报错原因', '原始行']],
                column_config={"修复折扣": st.column_config.NumberColumn("拟提报%", format="%d%%")},
                disabled=['ASIN', '状态', '原价', '要求净价', '报错原因', '原始行'],
                hide_index=True
            )

            # --- 生成文件 ---
            if st.button("🚀 生成修复版提报单"):
                selected = edited_df[edited_df['保留'] == True]
                if not selected.empty:
                    # 分组：原始行 + 修复折扣（没报错的会在一起，报错修复的会按新折扣分开）
                    output_data = []
                    for (orig_idx, disc), group in selected.groupby(['原始行', '修复折扣']):
                        orig_meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['meta'].iloc[0]
                        
                        output_data.append({
                            "ASIN 列表": ";".join(group['ASIN'].tolist()),
                            "折扣类型（满减€或折扣）": "折扣",
                            "优惠券“折扣”数值": disc,
                            "优惠券名称": f"Re-Fixed-{disc}%",
                            "优惠券预算": orig_meta.get('优惠券预算', 1000),
                            "优惠券开始日期": orig_meta.get('优惠券开始日期', ""),
                            "优惠券结束日期": orig_meta.get('优惠券结束日期', ""),
                            "限制每位买家只能兑换一次": "是"
                        })
                    
                    final_df = pd.DataFrame(output_data)
                    st.dataframe(final_df)
                    
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        final_df.to_excel(writer, index=False)
                    st.download_button("📥 点击下载", output.getvalue(), "Coupon_Fixed_Upload.xlsx")

if __name__ == "__main__":
    main()
