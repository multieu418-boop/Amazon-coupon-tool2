import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl
from copy import copy

st.set_page_config(page_title="Amazon Coupon 专家级修复工具", layout="wide")

# --- 1. 核心解析逻辑 ---
def parse_error_details(comment_text):
    error_map = {}
    if not comment_text: return error_map
    blocks = re.split(r'([A-Z0-9]{10})\n', str(comment_text))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            req_p_match = re.search(r'(?:要求的净价格|当前净价格|要求的最高商品价格)：[^\d]*([\d\.]+)', content)
            req_p = float(req_p_match.group(1)) if req_p_match else None
            reason_part = re.split(r'(?:要求的净价格|当前净价格|要求的最高商品价格)', content)[0]
            reason = reason_part.strip().replace('\n', ' ')
            error_map[asin] = {"req_price": req_p, "reason": reason}
    return error_map

# --- 2. 底稿读取 ---
def load_excel_template(file):
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active
    data = []
    headers = [cell.value for cell in ws[7]] 
    for row_idx, row in enumerate(ws.iter_rows(min_row=10), 10):
        row_values = [cell.value for cell in row]
        if not any(row_values): continue
        comment = row[-1].comment.text if row[-1].comment else ""
        row_dict = {headers[i]: val for i, val in enumerate(row_values) if i < len(headers)}
        row_dict['_comment'] = comment
        row_dict['_row_idx'] = row_idx
        data.append(row_dict)
    return pd.DataFrame(data), headers

# --- 3. Listing 读取 ---
def load_listing(file):
    for enc in ['utf-8', 'utf-16', 'gbk', 'utf-8-sig']:
        try:
            file.seek(0)
            df = pd.read_csv(file, sep='\t', encoding=enc) if file.name.endswith('.txt') else pd.read_excel(file)
            df.columns = [c.lower().strip() for c in df.columns]
            return df
        except: continue
    return None

def main():
    st.title("🎯 Amazon Coupon 终极全量修复系统")
    
    if 'master_df' not in st.session_state:
        st.session_state.master_df = None

    # --- 侧边栏 ---
    st.sidebar.header("⚙️ 筛选与预警配置")
    status_sel = st.sidebar.multiselect("1. ASIN 状态筛选", ["✅ 正常", "❌ 批注报错"], default=["✅ 正常", "❌ 批注报错"])
    discount_threshold = st.sidebar.slider("2. 折扣力度红色预警线 (%)", 5, 50, 30) / 100
    reason_keyword = st.sidebar.text_input("3. 报错原因关键词过滤")
    
    if st.sidebar.button("🔄 重置/重新上传文件"):
        st.session_state.master_df = None
        st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("上传带批注的报错模板", type=['xlsx'])

    if l_file and e_file:
        if st.session_state.master_df is None:
            df_l = load_listing(l_file)
            e_file.seek(0)
            df_ui, headers = load_excel_template(e_file)
            
            if df_l is not None and df_ui is not None:
                asin_col = next((c for c in df_l.columns if 'asin' in c), None)
                price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
                e_asin_idx = next((c for c in df_ui.columns if 'ASIN' in str(c)), df_ui.columns[0])
                e_disc_idx = next((c for c in df_ui.columns if '折扣' in str(c) and '数值' in str(c)), None)

                all_rows = []
                for _, row in df_ui.iterrows():
                    asins = [a.strip() for a in str(row.get(e_asin_idx, "")).replace(',', ';').split(';') if a.strip()]
                    err_map = parse_error_details(row.get('_comment'))
                    for a in asins:
                        p_match = df_l[df_l[asin_col] == a][price_col].values if asin_col else []
                        orig_p = p_match[0] if len(p_match) > 0 else None
                        info = err_map.get(a, {})
                        is_err = a in err_map
                        curr_d = row.get(e_disc_idx, 0.05)
                        suggested = curr_d
                        if is_err and orig_p and info.get('req_price'):
                            needed = math.ceil(((float(orig_p) - float(info.get('req_price'))) / float(orig_p)) * 100)
                            suggested = needed / 100 if curr_d < 1 else needed

                        all_rows.append({
                            "决策": "保留",
                            "ASIN": a,
                            "状态": "❌ 批注报错" if is_err else "✅ 正常",
                            "详细报错原因": info.get('reason', "-"),
                            "拟提报折扣": suggested,
                            "Listing原价": orig_p,
                            "要求净价": info.get('req_price'),
                            "原始行号": row.get('_row_idx'),
                            "meta_row": row.to_dict()
                        })
                st.session_state.master_df = pd.DataFrame(all_rows)
                st.session_state.orig_headers = headers

        if st.session_state.master_df is not None:
            # 应用筛选
            mask = st.session_state.master_df['状态'].isin(status_sel)
            if reason_keyword:
                mask = mask & st.session_state.master_df['详细报错原因'].str.contains(reason_keyword)
            
            # 建立一个带原始索引的视图
            df_filtered = st.session_state.master_df[mask].copy()

            st.subheader("🛠️ 修复决策台")
            
            # --- 修复核心：监听编辑器的变化 ---
            edited_data = st.data_editor(
                df_filtered,
                column_config={
                    "决策": st.column_config.SelectboxColumn("决策", options=["保留", "剔除"]),
                    "拟提报折扣": st.column_config.NumberColumn("拟提报折扣", format="%.2f"),
                    "详细报错原因": st.column_config.TextColumn("详细报错原因", width="large"),
                    "meta_row": None, "原始行号": None
                },
                disabled=['ASIN', '状态', '详细报错原因', 'Listing原价', '要求净价'],
                hide_index=False, # 显示索引以便内部对照
                use_container_width=True,
                key="coupon_editor"
            )

            # 强制即时同步：一旦检测到 editor 里的数据与视图不一致，立刻更新 master_df
            if not edited_data.equals(df_filtered):
                st.session_state.master_df.update(edited_data)
                # 使用 rerun 确保状态被强制固化，解决“点两次”的问题
                st.rerun()

            # 导出按钮
            st.markdown("---")
            if st.button("🚀 导出全量原格式文件 (包含正确+已保留报错项)"):
                e_file.seek(0)
                wb = openpyxl.load_workbook(e_file)
                ws = wb.active
                for r in range(10, ws.max_row + 1): ws.cell(row=r, column=1).value = None 

                # 导出所有决策为“保留”的项
                final_keep = st.session_state.master_df[st.session_state.master_df['决策'] == "保留"]
                
                if final_keep.empty:
                    st.warning("无可导出的保留项。")
                else:
                    a_idx, d_idx = 1, 3
                    for i, h in enumerate(st.session_state.orig_headers, 1):
                        if h and 'ASIN' in str(h): a_idx = i
                        if h and '折扣' in str(h) and '数值' in str(h): d_idx = i

                    curr_r = 10
                    for (orig_line, disc), group in final_keep.groupby
