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

# --- 2. 格式化无损导出函数 ---
def generate_excel(e_file, master_df, orig_headers):
    e_file.seek(0)
    wb = openpyxl.load_workbook(e_file)
    ws = wb.active
    # 清空旧数据
    for r in range(10, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            ws.cell(row=r, column=c).value = None

    final_keep = master_df[master_df['决策'] == "保留"]
    if final_keep.empty:
        return None

    # 定位列
    a_idx, d_idx = 1, 3
    for i, h in enumerate(orig_headers, 1):
        if h and 'ASIN' in str(h): a_idx = i
        if h and '折扣' in str(h) and '数值' in str(h): d_idx = i

    curr_r = 10
    for (orig_line, disc), group in final_keep.groupby(['原始行号', '拟提报折扣']):
        for c in range(1, len(orig_headers) + 1):
            source = ws.cell(row=orig_line, column=c)
            target = ws.cell(row=curr_r, column=c)
            target.value = source.value
            if source.has_style:
                target.font, target.border, target.fill = copy(source.font), copy(source.border), copy(source.fill)
                target.number_format, target.alignment = copy(source.number_format), copy(source.alignment)
        
        ws.cell(row=curr_r, column=a_idx).value = ";".join(group['ASIN'].tolist())
        ws.cell(row=curr_r, column=d_idx).value = disc
        curr_r += 1

    if ws.max_row >= curr_r:
        ws.delete_rows(curr_r, ws.max_row - curr_r + 1)
    
    out_io = io.BytesIO()
    wb.save(out_io)
    return out_io.getvalue()

def main():
    st.title("🎯 Amazon Coupon 自动化决策与无损修复系统")

    # 初始化持久状态
    if 'master_df' not in st.session_state:
        st.session_state.master_df = None
        st.session_state.orig_headers = None

    # --- 侧边栏 ---
    st.sidebar.header("⚙️ 筛选与预警配置")
    status_sel = st.sidebar.multiselect("1. ASIN 状态筛选", ["✅ 正常", "❌ 批注报错"], default=["✅ 正常", "❌ 批注报错"])
    discount_limit = st.sidebar.slider("2. 折扣力度红色预警线 (%)", 5, 50, 30) / 100
    reason_kw = st.sidebar.text_input("3. 报错原因关键词过滤")
    
    if st.sidebar.button("🔄 重置并重新上传"):
        for key in st.session_state.keys(): del st.session_state[key]
        st.rerun()

    # 文件上传
    l_file = st.file_uploader("1. 上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    e_file = st.file_uploader("2. 上传带批注的报错模板", type=['xlsx'])

    # 数据处理触发
    if l_file and e_file and st.session_state.master_df is None:
        with st.spinner("正在解析文件..."):
            wb = openpyxl.load_workbook(e_file, data_only=True)
            ws = wb.active
            headers = [cell.value for cell in ws[7]]
            
            # 读取 Listing 价格
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

    # 展示与导出（独立于上传逻辑之外，只要有数据就显示）
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
                "详细报错原因": st.column_config.TextColumn("详细报错原因", width="large"),
                "原始行号": None
            },
            disabled=['ASIN', '状态', '详细报错原因', 'Listing原价', '要求净价'],
            hide_index=True, use_container_width=True, key="editor_final"
        )

        # 同步数据
        if not edited.equals(df_filtered):
            for idx in edited.index:
                st.session_state.master_df.loc[idx, '决策'] = edited.loc[idx, '决策']
                st.session_state.master_df.loc[idx, '拟提报折扣'] = edited.loc[idx, '拟提报折扣']
            st.rerun()

        # --- 导出区：彻底独立出来 ---
        st.markdown("---")
        if st.button("🚀 点击生成导出文件", use_container_width=True):
            file_data = generate_excel(e_file, st.session_state.master_df, st.session_state.orig_headers)
            if file_data:
                st.success("✅ 文件生成成功！")
                st.download_button("📥 下载修复后的 Excel", file_data, "Coupon_Fixed.xlsx", "application/vnd.ms-excel")
            else:
                st.error("没有可保留的 ASIN 供导出。")

if __name__ == "__main__":
    main()
