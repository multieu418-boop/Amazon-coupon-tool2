import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl
from copy import copy

st.set_page_config(page_title="Amazon Coupon 自动化修复工具", layout="wide")

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
            auto_exclude = "没有经验证的参考价" in reason
            error_map[asin] = {"req_price": req_p, "reason": reason, "default_decision": "剔除" if auto_exclude else "保留"}
    return error_map

# --- 2. 增强型格式化无损导出函数 ---
def generate_excel(e_file, master_df, orig_headers):
    e_file.seek(0)
    wb = openpyxl.load_workbook(e_file)
    ws = wb.active
    
    # 1. 备份第10行开始的所有原始行数据对象，以便后续完整复制整行
    # key: 原始行号, value: 该行所有单元格的值
    row_data_backup = {}
    for r_idx in master_df['原始行号'].unique():
        row_cells = [ws.cell(row=r_idx, column=c).value for c in range(1, ws.max_column + 1)]
        row_data_backup[r_idx] = row_cells

    # 2. 清空底稿第10行以后所有数据（为了重新填入）
    max_r = ws.max_row
    if max_r >= 10:
        for r in range(10, max_r + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(row=r, column=c).value = None

    final_keep = master_df[master_df['决策'] == "保留"]
    if final_keep.empty:
        return None

    # 3. 定位 ASIN 和 折扣列的列索引
    a_idx, d_idx = 1, 3
    for i, h in enumerate(orig_headers, 1):
        if h and 'ASIN' in str(h): a_idx = i
        if h and '折扣' in str(h) and '数值' in str(h): d_idx = i

    # 4. 填入数据
    curr_r = 10
    # 按“原始行号”和“提报折扣”分组，确保同一行的 ASIN 重新聚合
    for (orig_line, disc), group in final_keep.groupby(['原始行号', '拟提报折扣']):
        # A. 完整复制原始行的所有列数据
        orig_row_values = row_data_backup.get(orig_line)
        if orig_row_values:
            for c_idx, val in enumerate(orig_row_values, 1):
                target_cell = ws.cell(row=curr_r, column=c_idx)
                target_cell.value = val
                
                # B. 复制样式（从原行对应的单元格）
                source_cell = ws.cell(row=orig_line, column=c_idx)
                if source_cell.has_style:
                    target_cell.font = copy(source_cell.font)
                    target_cell.border = copy(source_cell.border)
                    target_cell.fill = copy(source_cell.fill)
                    target_cell.number_format = copy(source_cell.number_format)
                    target_cell.alignment = copy(source_cell.alignment)
        
        # C. 精准覆盖 ASIN 串和折扣数值
        ws.cell(row=curr_r, column=a_idx).value = ";".join(group['ASIN'].tolist())
        ws.cell(row=curr_r, column=d_idx).value = disc
        curr_r += 1

    # 5. 删除多余行
    if ws.max_row >= curr_r:
        ws.delete_rows(curr_r, ws.max_row - curr_r + 1)
    
    out_io = io.BytesIO()
    wb.save(out_io)
    return out_io.getvalue()

def main():
    st.title("🎯 Amazon Coupon 自动化决策与全行无损修复")

    if 'master_df' not in st.session_state:
        st.session_state.master_df = None
        st.session_state.orig_headers = None

    # --- 侧边栏 ---
    st.sidebar.header("⚙️ 筛选与预警配置")
    status_sel = st.sidebar.multiselect("1. ASIN 状态筛选", ["✅ 正常", "❌ 批注报错"], default=["✅ 正常", "❌ 批注报错"])
    discount_limit = st.sidebar.slider("2. 折扣力度红色预警线 (%)", 5, 50, 30) / 100
    reason_kw = st.sidebar.text_input("3. 报错原因关键词过滤")
    
    if st.sidebar.button("🔄 重置并重新上传"):
        for key in list(st.session_state.keys()): del st.session_state[key]
        st.rerun()

    l_file = st.file_uploader("1. 上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    e_file = st.file_uploader("2. 上传带批注的报错模板", type=['xlsx'])

    if l_file and e_file and st.session_state.master_df is None:
        with st.spinner("正在深度解析模板信息..."):
            wb = openpyxl.load_workbook(e_file, data_only=True)
            ws = wb.active
            headers = [cell.value for cell in ws[7]]
            
            for enc in ['utf-8', 'utf-16', 'gbk', 'utf-8-sig']:
                try:
                    l_file.seek(0)
                    df_l = pd.read_csv(l_file, sep='\t', encoding=enc) if l_file.name.endswith('.txt') else pd.read_excel(l_file)
                    df_l.columns = [c.lower().strip() for c in df_l.columns]
                    break
                except: continue
            
            asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
            e_asin_col = next((c for c in headers if 'ASIN' in str(c)), headers[0])
            e_disc_col = next((c for c in headers if '折扣' in str(c) and '数值' in str(c)), None)

            rows = []
            for r_idx, row in enumerate(ws.iter_rows(min_row=10), 10):
                vals = [cell.value for cell in row]
                if not any(vals): continue
                comment = row[-1].comment.text if row[-1].comment else ""
                row_dict = {headers[i]: v for i, v in enumerate(vals) if i < len(headers)}
                
                asins = [a.strip() for a in str(row_dict.get(e_asin_col, "")).replace(',', ';').split(';') if a.strip()]
                err_map = parse_error_details(comment)
                
                for a in asins:
                    p_match = df_l[df_l[asin_col] == a][price_col].values if asin_col else []
                    orig_p = p_match[0] if len(p_match) > 0 else None
                    info = err_map.get(a, {})
                    is_err = a in err_map
                    curr_d = row_dict.get(e_disc_col, 0.05)
                    suggested = curr_d
                    if is_err and orig_p and info.get('req_price'):
                        needed = math.ceil(((float(orig_p) - float(info.get('req_price'))) / float(orig_p)) * 100)
                        suggested = needed / 100 if curr_d < 1 else needed

                    rows.append({
                        "决策": info.get('default_decision', "保留"),
                        "ASIN": a, "状态": "❌ 批注报错" if is_err else "✅ 正常",
                        "详细报错原因": info.get('reason', "-"), "拟提报折扣": suggested,
                        "Listing原价": orig_p, "要求净价": info.get('req_price'),
                        "原始行号": r_idx
                    })
            st.session_state.master_df = pd.DataFrame(rows)
            st.session_state.orig_headers = headers

    if st.session_state.master_df is not None:
        mask = st.session_state.master_df['状态'].isin(status_sel)
        if reason_kw:
            mask = mask & st.session_state.master_df['详细报错原因'].str.contains(reason_kw, case=False)
        
        df_filtered = st.session_state.master_df[mask].copy()

        st.subheader("🛠️ 修复决策台")
        edited = st.data_editor(
            df_filtered,
            column_config={
                "决策": st.column_config.SelectboxColumn("决策", options=["保留", "剔除"]),
                "拟提报折扣": st.column_config.NumberColumn("折扣数值", format="%.2f"),
                "详细报错原因": st.column_config.TextColumn("报错原因", width="large"),
                "原始行号": None
            },
            disabled=['ASIN', '状态', '详细报错原因', 'Listing原价', '要求净价'],
            hide_index=True, use_container_width=True, key="editor_vfinal"
        )

        if not edited.equals(df_filtered):
            for idx in edited.index:
                st.session_state.master_df.loc[idx, '决策'] = edited.loc[idx, '决策']
                st.session_state.master_df.loc[idx, '拟提报折扣'] = edited.loc[idx, '拟提报折扣']
            st.rerun()

        st.markdown("---")
        if st.button("🚀 生成并导出完整信息 Excel", use_container_width=True):
            file_data = generate_excel(e_file, st.session_state.master_df, st.session_state.orig_headers)
            if file_data:
                st.success("✅ 文件已成功生成，包含原始模板的所有行信息。")
                st.download_button("📥 点击下载修复后的完整 Excel", file_data, "Coupon_Full_Info_Fixed.xlsx")
            else:
                st.error("没有可导出的项。")

if __name__ == "__main__":
    main()
