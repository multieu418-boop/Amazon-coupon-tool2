import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl
from copy import copy

st.set_page_config(page_title="Amazon Coupon 全量无损修复工具", layout="wide")

# --- 1. 解析逻辑 ---
def parse_error_details(comment_text):
    error_map = {}
    if not comment_text: return error_map
    blocks = re.split(r'([A-Z0-9]{10})\n', str(comment_text))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            req_price_match = re.search(r'(?:要求的净价格|当前净价格)：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            reason_part = re.split(r'(?:要求的净价格|当前净价格)', content)[0]
            reason = reason_part.strip().replace('\n', ' ')
            error_map[asin] = {"req_price": req_price, "reason": reason}
    return error_map

# --- 2. 读取底稿 ---
def load_excel_with_meta(file):
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

# --- 3. Listing 匹配 ---
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
    st.title("🎯 Amazon Coupon 全量修复与决策系统")
    st.info("逻辑说明：即便在筛选状态下操作，所有‘保留/剔除’的选择也会被保存。导出时会自动包含‘正常ASIN’+‘已选保留的报错ASIN’。")

    # 初始化 Session State
    if 'master_data' not in st.session_state:
        st.session_state.master_data = None

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传带批注的报错模板", type=['xlsx'])

    if l_file and e_file:
        # 仅在第一次上传或点击重置时初始化数据
        if st.session_state.master_data is None:
            df_l = load_listing(l_file)
            e_file.seek(0)
            df_ui, orig_headers = load_excel_with_meta(e_file)
            
            if df_l is not None and df_ui is not None:
                asin_col = next((c for c in df_l.columns if 'asin' in c), None)
                price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
                e_asin_col = next((c for c in df_ui.columns if 'ASIN' in str(c)), df_ui.columns[0])
                e_disc_col = next((c for c in df_ui.columns if '折扣' in str(c) and '数值' in str(c)), None)

                all_rows = []
                for _, row in df_ui.iterrows():
                    asins = [a.strip() for a in str(row.get(e_asin_col, "")).replace(',', ';').split(';') if a.strip()]
                    err_map = parse_error_details(row.get('_comment'))
                    for a in asins:
                        p_match = df_l[df_l[asin_col] == a][price_col].values if asin_col else []
                        orig_p = p_match[0] if len(p_match) > 0 else None
                        info = err_map.get(a, {})
                        is_err = a in err_map
                        current_d = row.get(e_disc_col, 0.05)
                        suggested = current_d
                        if is_err and orig_p and info.get('req_price'):
                            needed = math.ceil(((float(orig_p) - float(info.get('req_price'))) / float(orig_p)) * 100)
                            suggested = needed / 100 if current_d < 1 else needed

                        all_rows.append({
                            "决策": "保留",
                            "ASIN": a,
                            "状态": "❌ 批注报错" if is_err else "✅ 正常",
                            "详细报错原因": info.get('reason', "-"),
                            "拟提报折扣": suggested,
                            "Listing原价": orig_p,
                            "要求净价": info.get('req_price'),
                            "原始行号": row.get('_row_idx'),
                            "meta": row.to_dict()
                        })
                st.session_state.master_data = pd.DataFrame(all_rows)
                st.session_state.orig_headers = orig_headers

        if st.session_state.master_data is not None:
            # --- 筛选功能 ---
            st.sidebar.header("🔍 视图筛选")
            status_filter = st.sidebar.multiselect("筛选状态", ["✅ 正常", "❌ 批注报错"], default=["✅ 正常", "❌ 批注报错"])
            reason_kw = st.sidebar.text_input("报错原因关键字过滤")
            
            # 从 Master Data 中过滤出显示数据
            mask = st.session_state.master_data['状态'].isin(status_filter)
            if reason_kw:
                mask = mask & st.session_state.master_data['详细报错原因'].str.contains(reason_kw)
            
            df_to_show = st.session_state.master_data[mask]

            # --- 决策台 ---
            st.subheader("🛠️ 决策操作区")
            # 关键：使用 on_change 机制或直接处理 data_editor 的返回
            edited_res = st.data_editor(
                df_to_show,
                column_config={
                    "决策": st.column_config.SelectboxColumn("决策", options=["保留", "剔除"]),
                    "拟提报折扣": st.column_config.NumberColumn("拟提报折扣", format="%.2f"),
                    "meta": None, # 隐藏隐藏列
                    "原始行号": None
                },
                disabled=['ASIN', '状态', '详细报错原因', 'Listing原价', '要求净价'],
                hide_index=True,
                use_container_width=True,
                key="editor_key"
            )

            # 同步修改回 Master Data (防止筛选消失)
            if not edited_res.equals(df_to_show):
                st.session_state.master_data.update(edited_res)

            # --- 导出功能 ---
            st.markdown("---")
            if st.button("🚀 导出全量文件（包含正常 + 您保留的报错 ASIN）"):
                # 重新加载底稿
                e_file.seek(0)
                wb = openpyxl.load_workbook(e_file)
                ws = wb.active
                # 清空底稿数据区
                for r in range(10, ws.max_row + 1): ws.cell(row=r, column=1).value = None 

                # 核心逻辑：从 Session State 的 Master Data 中拿数据
                # 即使没在屏幕上显示的（被筛选掉的），只要决策是“保留”，都会被导出
                final_keep_df = st.session_state.master_data[st.session_state.master_data['决策'] == "保留"]
                
                if final_keep_df.empty:
                    st.warning("当前没有选择保留任何 ASIN。")
                else:
                    # 获取列索引
                    a_idx, d_idx = 1, 3
                    for i, h in enumerate(st.session_state.orig_headers, 1):
                        if h and 'ASIN' in str(h): a_idx = i
                        if h and '折扣' in str(h) and '数值' in str(h): d_idx = i

                    curr_r = 10
                    # 按原始行和折扣合并
                    for (orig_line, disc), group in final_keep_df.groupby(['原始行号', '拟提报折扣']):
                        # 复制原行格式
                        for c in range(1, len(st.session_state.orig_headers) + 1):
                            source = ws.cell(row=orig_line, column=c)
                            target = ws.cell(row=curr_r, column=c)
                            target.value = source.value
                            if source.has_style:
                                target.font, target.border, target.fill = copy(source.font), copy(source.border), copy(source.fill)
                                target.number_format, target.alignment = copy(source.number_format), copy(source.alignment)
                        
                        # 填入新的 ASIN 组合和折扣
                        ws.cell(row=curr_r, column=a_idx).value = ";".join(group['ASIN'].tolist())
                        ws.cell(row=curr_r, column=d_idx).value = disc
                        curr_r += 1

                    if ws.max_row >= curr_r: ws.delete_rows(curr_r, ws.max_row - curr_r + 1)
                    
                    out = io.BytesIO()
                    wb.save(out)
                    st.success(f"导出成功！文件包含 {len(final_keep_df)} 个 ASIN。")
                    st.download_button("📥 点击下载全量修复文件", out.getvalue(), "Amazon_Fixed_All_ASINs.xlsx")

if __name__ == "__main__":
    main()
