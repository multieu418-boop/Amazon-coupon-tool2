import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 修复与重组工具", layout="wide")

# --- 辅助函数：解析报错信息 ---
def parse_error_details(error_msg):
    """解析 N 列中的报错详情，提取 ASIN 和 要求净价"""
    error_map = {}
    if pd.isna(error_msg): return error_map
    # 正则提取 ASIN 块
    blocks = re.split(r'([A-Z0-9]{10})\n', str(error_msg))
    for i in range(1, len(blocks), 2):
        asin = blocks[i].strip()
        content = blocks[i+1]
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
        req_price = float(req_price_match.group(1)) if req_price_match else None
        is_no_ref = "没有经验证" in content
        error_map[asin] = {"req_price": req_price, "is_no_ref": is_no_ref, "msg": content.strip()}
    return error_map

# --- 辅助函数：文件读取 ---
def load_data(file):
    if file is None: return None
    fname = file.name.lower()
    try:
        if fname.endswith('.txt'):
            for enc in ['utf-8', 'utf-16', 'gbk']:
                try:
                    file.seek(0)
                    return pd.read_csv(file, sep='\t', encoding=enc)
                except: continue
        elif fname.endswith('.csv'):
            return pd.read_csv(file)
        else:
            return pd.read_excel(file)
    except Exception as e:
        st.error(f"读取失败: {e}")
        return None

def main():
    st.title("🎯 Amazon Coupon 智能修复重组 (Phase 2)")
    st.info("功能：从报错 Coupon 中剔除错误 ASIN，并为修复后的 ASIN 创建新提报。")

    col_l, col_r = st.columns(2)
    with col_l:
        listing_file = st.file_uploader("1. 上传 All Listing 表", type=['txt', 'xlsx', 'csv'])
    with col_r:
        error_file = st.file_uploader("2. 上传亚马逊【报错返回模板】", type=['xlsx', 'csv'])

    if listing_file and error_file:
        df_l = load_data(listing_file)
        df_e = load_data(error_file)

        if df_l is not None and df_e is not None:
            # 列名清理
            df_l.columns = [str(c).lower().strip() for c in df_l.columns]
            df_e.columns = [str(c).lower().strip() for c in df_e.columns]

            # 自动定位关键列
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)
            e_asin_col = next((c for c in df_e.columns if 'asin list' in c or 'asin列表' in c), None)
            e_error_col = next((c for c in df_e.columns if 'error' in c or '处理结果' in c or '批注' in c), df_e.columns[-1])
            
            # 记录原始 Coupon 的其他信息（预算、时间、原始折扣）
            # 假设列名符合亚马逊标准
            other_cols = ['预算', '折扣', '开始日期', '结束日期', '名称', 'budget', 'discount percentage', 'start date', 'end date']
            found_other_cols = [c for c in df_e.columns if c in other_cols]

            # --- 解析与重组 ---
            rows = []
            for idx, e_row in df_e.iterrows():
                raw_asin_str = str(e_row[e_asin_col])
                error_msg = str(e_row[e_error_col])
                error_map = parse_error_details(error_msg)
                
                # 拆分 ASIN
                asins = [a.strip() for a in raw_asin_str.replace(',', ';').split(';') if a.strip()]
                
                for a in asins:
                    # 匹配原价
                    p_val = df_l[df_l[l_asin_col] == a][l_price_col].values
                    price = p_val[0] if len(p_val)>0 else None
                    
                    is_error = a in error_map
                    data = {
                        "原始Coupon行": idx + 1,
                        "ASIN": a,
                        "状态": "❌ 报错" if is_error else "✅ 正常",
                        "原价": price,
                        "要求净价": error_map[a]['req_price'] if is_error else None,
                        "报错原因": error_map[a]['msg'] if is_error else "正常",
                        "原始信息": e_row.to_dict() # 保存整行信息以便继承
                    }
                    rows.append(data)

            df_work = pd.DataFrame(rows)

            # --- 计算逻辑 ---
            def calc_new_discount(row):
                if row['状态'] == "✅ 正常":
                    # 继承原始折扣
                    return row['原始信息'].get('折扣百分比', row['原始信息'].get('discount percentage', 5))
                if "没有经验证" in row['报错原因']:
                    return 0 # 标记为待剔除
                if pd.notnull(row['原价']) and pd.notnull(row['要求净价']):
                    needed = math.ceil(((row['原价'] - row['要求净价']) / row['原价']) * 100)
                    return max(needed, 5)
                return 0

            df_work['最终折扣'] = df_work.apply(calc_new_discount, axis=1)
            df_work['确认保留'] = df_work['最终折扣'] > 0

            st.subheader("🛠️ ASIN 处理台")
            st.write("提示：正常的 ASIN 会保留原折扣，报错 ASIN 会更新为建议折扣。取消勾选即可剔除。")
            
            edited_df = st.data_editor(
                df_work[['确认保留', 'ASIN', '状态', '最终折扣', '原价', '要求净价', '报错原因', '原始Coupon行']],
                column_config={
                    "最终折扣": st.column_config.NumberColumn("拟用折扣%", format="%d%%"),
                    "确认保留": st.column_config.CheckboxColumn("保留?", default=True)
                },
                disabled=['ASIN', '状态', '原价', '要求净价', '报错原因', '原始Coupon行'],
                hide_index=True
            )

            # --- 生成最终结果 ---
            if st.button("🚀 重新生成提报数据"):
                final_submit = edited_df[edited_df['确认保留'] == True]
                
                # 按照 (原始行号 + 最终折扣) 进行聚合
                # 逻辑：同一行原始Coupon中，折扣相同的ASIN合并；如果折扣变了，则新开一行
                output_rows = []
                
                # 分组逻辑：原始Coupon来源 + 现在的折扣力度
                grouped = final_submit.groupby(['原始Coupon行', '最终折扣'])
                
                for (orig_line, discount), group in grouped:
                    # 拿到这一组 ASIN 的原始信息（取第一条即可，因为预算日期都一样）
                    orig_info = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['原始信息'].iloc[0]
                    
                    # 组合 ASIN 字符串
                    combined_asins = ";".join(group['ASIN'].tolist())
                    
                    new_row = {
                        "ASIN List": combined_asins,
                        "Discount Percentage": discount,
                        "Budget": orig_info.get('预算', orig_info.get('budget', 100)),
                        "Start Date": orig_info.get('开始日期', orig_info.get('start date', '')),
                        "End Date": orig_info.get('结束日期', orig_info.get('end date', '')),
                        "Name": f"Fixed-{orig_info.get('名称', orig_info.get('name', 'Coupon'))}-{discount}%",
                        "Source_Line": orig_line
                    }
                    output_rows.append(new_row)

                res_df = pd.DataFrame(output_rows)
                st.write("### 最终重组结果")
                st.dataframe(res_df)

                # 下载
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    res_df.to_excel(writer, index=False, sheet_name='修复结果')
                
                st.download_button("📥 下载修复后的提报模板", output.getvalue(), "Amazon_Fixed_Upload.xlsx")

if __name__ == "__main__":
    main()
