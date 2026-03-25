import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl

st.set_page_config(page_title="Amazon Coupon 批注剥离与决策系统", layout="wide")

# --- 1. 核心解析逻辑：从批注文本中提取 ASIN 和 净价格 ---
def parse_error_from_comment(comment_text):
    error_map = {}
    if not comment_text: return error_map
    blocks = re.split(r'([A-Z0-9]{10})\n', str(comment_text))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            error_map[asin] = {"req_price": req_price, "msg": content.strip()}
    else:
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', str(comment_text))
        req_price = float(req_price_match.group(1)) if req_price_match else None
        error_map["GLOBAL"] = {"req_price": req_price, "msg": str(comment_text)}
    return error_map

# --- 2. 带批注的 Excel 读取（保留原始结构） ---
def load_template_with_comments(file):
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        data = []
        # 第7行是表头
        headers = [cell.value for cell in ws[7]]
        # 从第10行开始读取
        for row in ws.iter_rows(min_row=10):
            row_values = [cell.value for cell in row]
            if not any(row_values): continue
            
            # 提取最后一列（N列）的批注
            last_cell = row[-1]
            comment_text = last_cell.comment.text if last_cell.comment else ""
            
            row_dict = {headers[i]: val for i, val in enumerate(row_values)}
            row_dict['_comment_error'] = comment_text
            data.append(row_dict)
        return pd.DataFrame(data), headers
    except Exception as e:
        st.error(f"解析报错模板失败: {e}")
        return None, None

# --- 3. Listing 读取 ---
def load_listing(file):
    if file is None: return None
    for enc in ['utf-8', 'utf-16', 'gbk']:
        try:
            file.seek(0)
            if file.name.endswith('.txt'):
                return pd.read_csv(file, sep='\t', encoding=enc)
            else:
                return pd.read_excel(file)
        except: continue
    return None

def main():
    st.title("🎯 Amazon Coupon 精准决策修复工具")
    
    # 侧边栏设置
    st.sidebar.header("🔍 筛选与预警")
    status_filter = st.sidebar.multiselect("1. 筛选 ASIN 状态", ["✅ 正常", "❌ 批注报错"], default=["✅ 正常", "❌ 批注报错"])
    threshold = st.sidebar.slider("2. 高折扣红色预警线 (%)", 5, 50, 30) / 100

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 报告 (查原价)", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传带【批注】的报错模板", type=['xlsx'])

    if l_file and e_file:
        df_l = load_listing(l_file)
        df_e, original_headers = load_template_with_comments(e_file)

        if df_l is not None and df_e is not None:
            # 标准化 Listing 列名
            df_l.columns = [c.lower() for c in df_l.columns]
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
            
            # 定位模板列
            e_asin_list_col = next((c for c in df_e.columns if 'ASIN' in c), df_e.columns[0])
            e_disc_col = next((c for c in df_e.columns if '折扣' in c and '数值' in c), None)

            # --- 扁平化处理：将一行多个 ASIN 铺开供决策 ---
            rows = []
            for idx, row in df_e.iterrows():
                asins = [a.strip() for a in str(row.get(e_asin_list_col, "")).replace(',', ';').split(';') if a.strip()]
                err_map = parse_error_from_comment(row.get('_comment_error', ""))
                
                for a in asins:
                    p_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    origin_price = p_match[0] if len(p_match) > 0 else None
                    is_bad = a in err_map or ("GLOBAL" in err_map and len(asins) == 1)
                    info = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    # 状态计算
                    status = "❌ 批注报错" if is_bad else "✅ 正常"
                    
                    # 建议折扣计算
                    suggested = row.get(e_disc_col)
                    if is_bad and origin_price and info.get('req_price'):
                        suggested = math.ceil(((float(origin_price) - float(info.get('req_price'))) / float(origin_price)) * 100)
                        if row.get(e_disc_col) < 1: suggested = suggested / 100 # 兼容 0.15 格式

                    rows.append({
                        "决策": "保留修复",
                        "ASIN": a,
                        "状态": status,
                        "拟提报折扣": suggested,
                        "Listing原价": origin_price,
                        "要求净价": info.get('req_price'),
                        "批注原文": info.get('msg', ""),
                        "原始行": idx + 10,
                        "meta": row.to_dict()
                    })

            df_work = pd.DataFrame(rows)
            
            # 应用筛选
            df_display = df_work[df_work['状态'].isin(status_filter)]

            st.subheader("🛠️ 每个 ASIN 的修复决策")
            st.caption("注：你可以修改‘拟提报折扣’或将决策改为‘剔除’。高折扣行会以红色警示。")

            # 样式处理
            def style_row(row):
                if row['拟提报折扣'] > threshold:
                    return ['color: red; font-weight: bold'] * len(row)
                return [''] * len(row)

            edited_df = st.data_editor(
                df_display[['决策', 'ASIN', '状态', '拟提报折扣', 'Listing原价', '要求净价', '批注原文', '原始行']],
                column_config={
                    "决策": st.column_config.SelectboxColumn("决策", options=["保留修复", "剔除"]),
                    "拟提报折扣": st.column_config.NumberColumn("拟提报折扣", format="%.2f")
                },
                disabled=['ASIN', '状态', 'Listing原价', '要求净价', '批注原文', '原始行'],
                hide_index=True,
                use_container_width=True
            )

            # --- 4. 导出逻辑（严格遵循原格式） ---
            if st.button("🚀 按照原文件格式导出"):
                keep = edited_df[edited_df['决策'] == "保留修复"]
                if keep.empty:
                    st.warning("没有勾选保留任何 ASIN。")
                else:
                    output_rows = []
                    # 按原始行和折扣合并（剥离逻辑）
                    # 即使是同一个原始行的ASIN，如果决策后的折扣不同，也要拆成两行
                    for (orig_line, disc), group in keep.groupby(['原始行', '拟提报折扣']):
                        # 获取该行原始的所有元数据
                        meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['meta'].iloc[0]
                        new_row = meta.copy()
                        # 仅保留用户选择的 ASIN 列表
                        new_row[e_asin_list_col] = ";".join(group['ASIN'].tolist())
                        # 更新折扣
                        new_row[e_disc_col] = disc
                        # 移除辅助列（以_开头的）
                        final_clean_row = {k: v for k, v in new_row.items() if not str(k).startswith('_')}
                        output_rows.append(final_clean_row)
                    
                    res_df = pd.DataFrame(output_rows)
                    # 重新排列列顺序，确保和原模板 header 完全一致
                    res_df = res_df[original_headers]
                    
                    st.write("### 导出预览 (已根据你的选择重新组合)")
                    st.dataframe(res_df)
                    
                    out_io = io.BytesIO()
                    with pd.ExcelWriter(out_io, engine='openpyxl') as writer:
                        res_df.to_excel(writer, index=False)
                    st.download_button("📥 下载修复后的原格式文件", out_io.getvalue(), "Amazon_Fixed_Template.xlsx")

if __name__ == "__main__":
    main()
