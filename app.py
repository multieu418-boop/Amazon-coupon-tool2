import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl
from copy import copy

st.set_page_config(page_title="Amazon Coupon 专家修复工具", layout="wide")

# --- 1. 核心解析逻辑：抓取 ASIN 换行后到“价格”前的话 ---
def parse_error_details(comment_text):
    error_map = {}
    if not comment_text: return error_map
    
    # 匹配 10位ASIN + 换行
    blocks = re.split(r'([A-Z0-9]{10})\n', str(comment_text))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            
            # 提取要求的净价格
            req_price_match = re.search(r'(?:要求的净价格|当前净价格)：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            
            # 提取报错原因：抓取 ASIN 换行后，到“价格”字样之前的所有文本
            # 过滤掉“商品未通过优惠券定价验证”等冗余，只取核心原因
            reason_part = re.split(r'(?:要求的净价格|当前净价格)', content)[0]
            reason = reason_part.strip().replace('\n', ' ')
            
            error_map[asin] = {"req_price": req_price, "reason": reason}
    return error_map

# --- 2. 无损读取底稿 ---
def load_excel_with_meta(file):
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active
    data = []
    headers = [cell.value for cell in ws[7]] # 第7行表头
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
    st.title("🎯 Amazon Coupon 精准修复与无损导出台")
    
    # --- 侧边栏：之前的需求回归 ---
    st.sidebar.header("🔍 筛选与预警设置")
    status_sel = st.sidebar.multiselect("1. ASIN 状态筛选", ["✅ 正常", "❌ 批注报错"], default=["✅ 正常", "❌ 批注报错"])
    reason_kw = st.sidebar.text_input("2. 报错原因关键词过滤 (如: 参考价)")
    discount_limit = st.sidebar.slider("3. 折扣力度红色预警线 (%)", 5, 50, 30) / 100

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("上传 All Listing (查原价)", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("上传带批注的报错模板", type=['xlsx'])

    if l_file and e_file:
        df_l = load_listing(l_file)
        e_file.seek(0)
        df_ui, orig_headers = load_excel_with_meta(e_file)

        if df_l is not None and df_ui is not None:
            asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
            e_asin_col = next((c for c in df_ui.columns if 'ASIN' in str(c)), df_ui.columns[0])
            e_disc_col = next((c for c in df_ui.columns if '折扣' in str(c) and '数值' in str(c)), None)

            # 铺开数据
            all_rows = []
            for _, row in df_ui.iterrows():
                asins = [a.strip() for a in str(row.get(e_asin_col, "")).replace(',', ';').split(';') if a.strip()]
                err_map = parse_error_details(row.get('_comment'))
                
                for a in asins:
                    p_match = df_l[df_l[asin_col] == a][price_col].values if asin_col else []
                    orig_p = p_match[0] if len(p_match) > 0 else None
                    info = err_map.get(a, {})
                    is_err = a in err_map
                    
                    # 自动算折扣
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

            df_work = pd.DataFrame(all_rows)
            
            # --- 应用侧边栏筛选 ---
            mask = df_work['状态'].isin(status_sel)
            if reason_kw:
                mask = mask & df_work['详细报错原因'].str.contains(reason_kw)
            df_display = df_work[mask]

            st.subheader("🛠️ ASIN 决策台")
            # 样式：超过门槛标红
            def highlight_discount(s):
                return ['color: red; font-weight: bold' if (isinstance(v, float) and v > discount_limit) else '' for v in s]

            edited_df = st.data_editor(
                df_display[['决策', 'ASIN', '状态', '详细报错原因', '拟提报折扣', 'Listing原价', '要求净价', '原始行号']],
                column_config={
                    "决策": st.column_config.SelectboxColumn("决策", options=["保留", "剔除"]),
                    "拟提报折扣": st.column_config.NumberColumn("拟提报折扣", format="%.2f"),
                    "详细报错原因": st.column_config.TextColumn("详细报错原因", width="large")
                },
                disabled=['ASIN', '状态', '详细报错原因', 'Listing原价', '要求净价', '原始行号'],
                hide_index=True,
                use_container_width=True
            )

            # --- 导出逻辑：全量保留 + 修复剥离 ---
            if st.button("🚀 格式无损导出 (包含正确+已保留报错ASIN)"):
                e_file.seek(0)
                wb = openpyxl.load_workbook(e_file)
                ws = wb.active
                for r in range(10, ws.max_row + 1): ws.cell(row=r, column=1).value = None 

                # 整合所有保留的 ASIN（包括没在当前筛选页显示的）
                # 这里逻辑：edited_df 是当前页，我们要把 edited_df 的修改应用回总表，再导出总表中所有“保留”的
                keep_asins = edited_df[edited_df['决策'] == "保留"]
                
                # 确定列索引
                a_idx, d_idx = 1, 3
                for i, h in enumerate(orig_headers, 1):
                    if h and 'ASIN' in str(h): a_idx = i
                    if h and '折扣' in str(h) and '数值' in str(h): d_idx = i

                curr_r = 10
                for (orig_line, disc), group in keep_asins.groupby(['原始行号', '拟提报折扣']):
                    # 复制格式
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

                if ws.max_row >= curr_r: ws.delete_rows(curr_r, ws.max_row - curr_r + 1)
                
                out = io.BytesIO()
                wb.save(out)
                st.success(f"导出成功！已处理 {len(keep_asins)} 个 ASIN。")
                st.download_button("📥 下载修复后的 Excel", out.getvalue(), "Amazon_Coupon_Final.xlsx")

if __name__ == "__main__":
    main()
