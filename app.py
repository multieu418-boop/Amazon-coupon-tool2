import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 智能修复专家", layout="wide")

# --- 辅助函数：解析亚马逊复杂的报错文本 ---
def parse_error_details(error_msg):
    error_map = {}
    if pd.isna(error_msg) or str(error_msg).strip() == "": 
        return error_map
    
    # 正则提取：ASIN(10位) + 换行 + 错误内容
    blocks = re.split(r'([A-Z0-9]{10})\n', str(error_msg))
    for i in range(1, len(blocks), 2):
        asin = blocks[i].strip()
        content = blocks[i+1]
        # 匹配：要求的净价格：€37.99 或 要求的净价格：37.99
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
        req_price = float(req_price_match.group(1)) if req_price_match else None
        is_no_ref = "没有经验证" in content
        error_map[asin] = {"req_price": req_price, "is_no_ref": is_no_ref, "msg": content.strip()}
    return error_map

# --- 辅助函数：智能读取（自动跳过亚马逊模板的说明行） ---
def load_amazon_file(file):
    if file is None: return None
    try:
        # 先读取前15行探测表头位置
        df_preview = pd.read_excel(file, header=None, nrows=15)
        header_row = 0
        for i, row in df_preview.iterrows():
            # 只要某一行包含 "ASIN" 或 "折扣" 等关键字，就认定这行是表头
            row_str = str(row.values).lower()
            if 'asin' in row_str or 'discount' in row_str or '折扣' in row_str:
                header_row = i
                break
        
        file.seek(0)
        df = pd.read_excel(file, header=header_row)
        # 清理列名
        df.columns = [str(c).strip().lower() for c in df.columns]
        return df
    except Exception as e:
        st.error(f"文件读取失败: {e}")
        return None

# --- 函数：Listing文件读取 (带编码重试) ---
def load_listing_file(file):
    if file is None: return None
    fname = file.name.lower()
    try:
        if fname.endswith('.txt'):
            for enc in ['utf-8', 'utf-16', 'gbk', 'utf-8-sig']:
                try:
                    file.seek(0)
                    df = pd.read_csv(file, sep='\t', encoding=enc)
                    if not df.empty: return df
                except: continue
        elif fname.endswith('.csv'):
            return pd.read_csv(file)
        else:
            return pd.read_excel(file)
    except Exception as e:
        st.error(f"Listing读取失败: {e}")
        return None

def main():
    st.title("🎯 Amazon Coupon 报错自动修复 (第二阶段)")
    st.markdown("---")

    col_a, col_b = st.columns(2)
    with col_a:
        l_file = st.file_uploader("1. 上传 All Listing 表", type=['txt', 'xlsx', 'csv'])
    with col_b:
        e_file = st.file_uploader("2. 上传亚马逊【报错返回模板】", type=['xlsx'])

    if l_file and e_file:
        df_l = load_listing_file(l_file)
        df_e = load_amazon_file(e_file)

        if df_l is not None and df_e is not None:
            # 1. 自动识别 Listing 的列
            df_l.columns = [str(c).lower().strip() for c in df_l.columns]
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)

            # 2. 自动识别 报错模板 的列
            # 亚马逊报错列通常叫 'processing summary' 或在最后一列
            e_asin_col = next((c for c in df_e.columns if 'asin list' in c or 'asin列表' in c), None)
            e_error_col = next((c for c in df_e.columns if 'error' in c or '处理结果' in c or '批注' in c or 'summary' in c), df_e.columns[-1])
            
            # 提取原模板信息（用于继承日期、预算等）
            info_cols = {
                'budget': next((c for c in df_e.columns if 'budget' in c or '预算' in c), None),
                'discount': next((c for c in df_e.columns if 'discount' in c or '折扣' in c), None),
                'start': next((c for c in df_e.columns if 'start' in c or '开始' in c), None),
                'end': next((c for c in df_e.columns if 'end' in c or '结束' in c), None),
                'name': next((c for c in df_e.columns if 'name' in c or '名称' in c), None)
            }

            # 3. 解析与拆分
            all_rows = []
            for idx, row in df_e.iterrows():
                asin_str = str(row.get(e_asin_col, ""))
                if asin_str == "nan" or not asin_str: continue
                
                error_msg = str(row.get(e_error_col, ""))
                error_map = parse_error_details(error_msg)
                
                # 拆分此 Coupon 下的所有 ASIN
                asins = [a.strip() for a in asin_str.replace(',', ';').split(';') if a.strip()]
                
                for a in asins:
                    # 匹配原价
                    price_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    price = price_match[0] if len(price_match) > 0 else None
                    
                    is_err = a in error_map
                    all_rows.append({
                        "原始行": idx + 1,
                        "ASIN": a,
                        "状态": "❌ 报错" if is_err else "✅ 正常",
                        "原价": price,
                        "要求净价": error_map[a]['req_price'] if is_err else None,
                        "报错原因": error_map[a]['msg'] if is_err else "正常",
                        "原始折扣": row.get(info_cols['discount'], 5),
                        "row_data": row.to_dict()
                    })

            df_work = pd.DataFrame(all_rows)

            # 4. 计算逻辑
            def get_suggested(r):
                if r['状态'] == "✅ 正常": return r['原始折扣']
                if "没有经验证" in r['报错原因']: return 0 # 待剔除
                if pd.notnull(r['原价']) and pd.notnull(r['要求净价']):
                    needed = math.ceil(((r['原价'] - r['要求净价']) / r['原价']) * 100)
                    return max(needed, 5)
                return 0

            df_work['拟用折扣'] = df_work.apply(get_suggested, axis=1)
            df_work['保留'] = df_work['拟用折扣'] > 0

            # 5. UI 展示
            st.subheader("🛠️ ASIN 修复决策台")
            edited_df = st.data_editor(
                df_work[['保留', 'ASIN', '状态', '拟用折扣', '原价', '要求净价', '报错原因', '原始行']],
                column_config={
                    "拟用折扣": st.column_config.NumberColumn("拟提报折扣%", format="%d%%"),
                    "保留": st.column_config.CheckboxColumn("保留?", default=True)
                },
                disabled=['ASIN', '状态', '原价', '要求净价', '报错原因', '原始行'],
                hide_index=True
            )

            # 6. 重组导出
            if st.button("🚀 归纳并生成提报文件"):
                final = edited_df[edited_df['保留'] == True]
                if final.empty:
                    st.warning("请勾选需要提报的 ASIN")
                else:
                    # 分组：原始行 + 最终折扣（确保同一Coupon下折扣一致的合并，不一致的分开）
                    results = []
                    grouped = final.groupby(['原始行', '拟用折扣'])
                    for (orig_idx, disc), group in grouped:
                        # 找到这一行的原始元数据
                        orig_meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['row_data'].iloc[0]
                        
                        results.append({
                            "ASIN 列表": ";".join(group['ASIN'].tolist()),
                            "折扣百分比": disc,
                            "每位客户兑换次数限制": "是",
                            "预算": orig_meta.get(info_cols['budget'], 1000),
                            "优惠券名称": f"Fixed-{orig_meta.get(info_cols['name'], 'Coupon')}-{disc}%",
                            "开始日期": orig_meta.get(info_cols['start'], "2026-04-01"),
                            "结束日期": orig_meta.get(info_cols['end'], "2026-04-30")
                        })
                    
                    out_df = pd.DataFrame(results)
                    st.write("### 预览归纳后的结果")
                    st.dataframe(out_df)
                    
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        out_df.to_excel(writer, index=False, sheet_name='修复结果')
                    st.download_button("📥 下载修复后的提报文件", output.getvalue(), "Amazon_Fixed_Coupon.xlsx")

if __name__ == "__main__":
    main()
