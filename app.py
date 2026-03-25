import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 智能修复专家", layout="wide")

# --- 工具函数：解析报错文本 ---
def parse_error_msg(msg):
    """从亚马逊单行报错信息中提取要求净价"""
    if pd.isna(msg): return None
    # 提取：要求的净价格：€37.99
    match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', str(msg))
    return float(match.group(1)) if match else None

# --- 工具函数：读取各种格式 ---
def load_data(file, is_listing=False):
    if file is None: return None
    fname = file.name.lower()
    df = None
    try:
        if fname.endswith('.txt'):
            for enc in ['utf-8', 'utf-16', 'gbk']:
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
            df.columns = [str(c).lower().strip() for c in df.columns]
        return df
    except Exception as e:
        st.error(f"解析文件 {fname} 失败: {e}")
        return None

def main():
    st.title("🎯 Amazon Coupon 第二阶段：报错文件全量修复系统")
    st.markdown("---")

    # 1. 文件上传区
    col_a, col_b = st.columns(2)
    with col_a:
        listing_file = st.file_uploader("1. 上传 All Listing (获取原价)", type=['txt', 'xlsx', 'csv'])
    with col_b:
        error_file = st.file_uploader("2. 上传亚马逊返回的【报错模板】", type=['xlsx', 'csv'])

    if listing_file and error_file:
        df_l = load_data(listing_file)
        df_e = load_data(error_file)

        if df_l is not None and df_e is not None:
            # 智能识别列名
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)
            
            # 报错模板列名（根据附件结构）
            e_asin_col = next((c for c in df_e.columns if 'asin' in c and 'list' in c), None) # 通常是 'asin list'
            e_error_col = next((c for c in df_e.columns if 'error' in c or '处理结果' in c or '批注' in c), df_e.columns[-1])
            
            if not l_asin_col or not l_price_col or not e_asin_col:
                st.error("无法识别必要的 ASIN 或价格列，请检查文件表头。")
                return

            # --- 2. 核心解析逻辑 ---
            processed_data = []
            
            # 遍历报错模板的每一行 (每一个原本的 Coupon)
            for idx, row in df_e.iterrows():
                asins_str = str(row[e_asin_col])
                error_msg = str(row[e_error_col]) if e_error_col in df_e.columns else ""
                
                # 提取这一行中报错的 ASIN 及其要求价格
                # 示例：B0FZTVQY6M: 提高折扣...要求净价:37.99; B0FF9WSPQ5: 无参考价
                found_errors = {} 
                # 解析报错字符串中的 ASIN 块
                error_blocks = re.split(r'([A-Z0-9]{10})\s*\n', error_msg)
                for i in range(1, len(error_blocks), 2):
                    asin_err = error_blocks[i].strip()
                    content_err = error_blocks[i+1]
                    req_p = parse_error_msg(content_err)
                    is_no_ref = "没有经验证" in content_err
                    found_errors[asin_err] = {"req_p": req_p, "is_no_ref": is_no_ref}

                # 拆分原始提报的所有 ASIN
                all_asins = [a.strip() for a in asins_str.replace(',', ';').split(';') if a.strip()]
                
                for a in all_asins:
                    item = {
                        "Coupon行号": idx + 1,
                        "ASIN": a,
                        "原始状态": "✅ 正常" if a not in found_errors else "❌ 报错",
                        "要求净价": found_errors[a]["req_p"] if a in found_errors else None,
                        "原因": "正常/无需调整" if a not in found_errors else ("无参考价" if found_errors[a]["is_no_ref"] else "力度不足")
                    }
                    # 匹配原价
                    price_row = df_l[df_l[l_asin_col] == a]
                    item["原价"] = price_row[l_price_col].values[0] if not price_row.empty else None
                    processed_data.append(item)

            df_final = pd.DataFrame(processed_data)

            # --- 3. 计算建议折扣 ---
            def calc_logic(row):
                if row['原始状态'] == "✅ 正常":
                    return "保持原状", 0  # 正常的不强制改力度，后续按原逻辑合并
                if row['原因'] == "无参考价":
                    return "必须剔除", 0
                if pd.notnull(row['原价']) and pd.notnull(row['要求净价']):
                    needed = math.ceil(((row['原价'] - row['要求净价']) / row['原价']) * 100)
                    return f"调至 {max(needed, 5)}%", max(needed, 5)
                return "无法计算", 0

            df_final['处理建议'], df_final['拟用折扣'] = zip(*df_final.apply(calc_logic, axis=1))
            # 默认勾选：正常的勾选，报错但有力度的勾选，无参考价不勾选
            df_final['确认提报'] = df_final.apply(lambda r: True if r['拟用折扣'] > 0 or r['原始状态'] == "✅ 正常" else False, axis=1)

            # --- 4. 工作台展示 ---
            st.subheader("🛠️ ASIN 决策工作台")
            st.write("勾选“确认提报”以保留 ASIN，取消勾选则将其剔除。")
            
            # 编辑器
            edited_df = st.data_editor(
                df_final,
                column_config={
                    "确认提报": st.column_config.CheckboxColumn("确认提报?", default=False),
                    "拟用折扣": st.column_config.NumberColumn("折扣%", format="%d%%"),
                    "原价": st.column_config.NumberColumn("原价", format="%.2f"),
                    "要求净价": st.column_config.NumberColumn("亚马逊要求价", format="%.2f"),
                },
                disabled=["Coupon行号", "ASIN", "原始状态", "原因", "处理建议", "原价", "要求净价"],
                hide_index=True,
                use_container_width=True
            )

            # --- 5. 归纳导出 ---
            st.divider()
            if st.button("📦 重新归纳并生成提报文件"):
                # 1. 提取所有被勾选的 ASIN
                submit_df = edited_df[edited_df['确认提报'] == True]
                
                if submit_df.empty:
                    st.warning("没有勾选任何可提报的 ASIN。")
                else:
                    # 分两部分处理：
                    # A. 正常的 ASIN：需要保留原本的折扣力度（假设你第一阶段填写的折扣在报错文件里有，如果没有，则统一分配）
                    # 为了简化，我们按“拟用折扣”进行统一重组
                    # 注意：正常的 ASIN 如果拟用折扣是 0，说明它们需要按原计划提报。
                    
                    # 我们可以统一按“拟用折扣”分组，如果是“保持原状”的，可以由你手动在表格里填入一个数值
                    re_grouped = submit_df.groupby('拟用折扣')['ASIN'].apply(lambda x: ';'.join(list(set(x)))).reset_index()
                    
                    st.write("### 归纳下载区")
                    st.dataframe(re_grouped)
                    
                    # 统计信息
                    total_groups = len(re_grouped)
                    st.success(f"归纳完成！共生成 {total_groups} 组 Coupon 提报数据。")

                    # 下载 Excel
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        out_df = pd.DataFrame({
                            "ASIN List": re_grouped['ASIN'],
                            "Discount Percentage": re_grouped['拟用折扣'],
                            "Name": [f"Fixed Coupon {d}%" for d in re_grouped['拟用折扣']],
                            "Budget": 1000
                        })
                        out_df.to_excel(writer, index=False, sheet_name='Sheet1')
                    
                    st.download_button(
                        label="📥 下载修复后的提报模板",
                        data=output.getvalue(),
                        file_name="Amazon_Coupon_Fixed_Final.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

if __name__ == "__main__":
    main()
