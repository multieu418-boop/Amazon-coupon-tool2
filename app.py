import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl
from copy import copy

st.set_page_config(page_title="Amazon Coupon 格式无损修复工具", layout="wide")

# --- 1. 批注解析函数 ---
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

# --- 2. 读取逻辑 (用于决策台显示) ---
def load_data_for_ui(file):
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active
    data = []
    headers = [cell.value for cell in ws[7]]
    for row_idx, row in enumerate(ws.iter_rows(min_row=10), 10):
        row_values = [cell.value for cell in row]
        if not row_values[0]: continue
        comment_text = row[-1].comment.text if row[-1].comment else ""
        row_dict = {headers[i]: val for i, val in enumerate(row_values)}
        row_dict['_comment_error'] = comment_text
        row_dict['_orig_row_idx'] = row_idx # 记录在 Excel 中的原始行号
        data.append(row_dict)
    return pd.DataFrame(data), headers

def main():
    st.title("🎯 Amazon Coupon 格式无损修复（全量导出版）")
    
    st.sidebar.header("🔍 筛选与预警")
    status_filter = st.sidebar.multiselect("筛选 ASIN 状态", ["✅ 正常", "❌ 批注报错"], default=["✅ 正常", "❌ 批注报错"])
    threshold = st.sidebar.slider("折扣红色预警线 (%)", 5, 50, 30) / 100

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传带【批注】的报错模板", type=['xlsx'])

    if l_file and e_file:
        # 读取 Listing
        df_l = None
        for enc in ['utf-8', 'utf-16', 'gbk']:
            try:
                l_file.seek(0)
                df_l = pd.read_csv(l_file, sep='\t', encoding=enc) if l_file.name.endswith('.txt') else pd.read_excel(l_file)
                break
            except: continue
        
        # 读取模板数据
        e_file.seek(0)
        df_ui, original_headers = load_data_for_ui(e_file)

        if df_l is not None and df_ui is not None:
            df_l.columns = [c.lower() for c in df_l.columns]
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
            e_asin_list_col = next((c for c in df_ui.columns if 'ASIN' in c), df_ui.columns[0])
            e_disc_col = next((c for c in df_ui.columns if '折扣' in c and '数值' in c), None)

            # --- 扁平化 ASIN 用于 UI 决策 ---
            rows = []
            for _, row in df_ui.iterrows():
                asins = [a.strip() for a in str(row.get(e_asin_list_col, "")).replace(',', ';').split(';') if a.strip()]
                err_map = parse_error_from_comment(row.get('_comment_error', ""))
                for a in asins:
                    p_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    origin_price = p_match[0] if len(p_match) > 0 else None
                    is_bad = a in err_map or ("GLOBAL" in err_map and len(asins) == 1)
                    info = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    status = "❌ 批注报错" if is_bad else "✅ 正常"
                    # 计算建议折扣
                    suggested = row.get(e_disc_col)
                    if is_bad and origin_price and info.get('req_price'):
                        needed = math.ceil(((float(origin_price) - float(info.get('req_price'))) / float(origin_price)) * 100)
                        suggested = needed / 100 if row.get(e_disc_col) < 1 else needed

                    rows.append({
                        "决策": "保留",
                        "ASIN": a,
                        "状态": status,
                        "拟提报折扣": suggested,
                        "Listing原价": origin_price,
                        "要求净价": info.get('req_price'),
                        "原始行号": row.get('_orig_row_idx'),
                        "meta": row.to_dict()
                    })

            df_work = pd.DataFrame(rows)
            df_display = df_work[df_work['状态'].isin(status_filter)]

            st.subheader("🛠️ ASIN 修复决策台")
            edited_df = st.data_editor(
                df_display[['决策', 'ASIN', '状态', '拟提报折扣', 'Listing原价', '要求净价', '原始行号']],
                column_config={
                    "决策": st.column_config.SelectboxColumn("决策", options=["保留", "剔除"]),
                    "拟提报折扣": st.column_config.NumberColumn("拟提报折扣", format="%.2f")
                },
                disabled=['ASIN', '状态', 'Listing原价', '要求净价', '原始行号'],
                hide_index=True,
                use_container_width=True
            )

            # --- 3. 核心导出逻辑：操作原始文件对象 ---
            if st.button("🚀 格式无损导出 (含正确+报错保留)"):
                e_file.seek(0)
                wb_out = openpyxl.load_workbook(e_file)
                ws_out = wb_out.active
                
                # A. 先清空原表第10行以后的所有 ASIN 列表格，方便重新填充
                for r in range(10, ws_out.max_row + 1):
                    ws_out.cell(row=r, column=1).value = None 

                # B. 整合用户决策
                # 找出所有标记为“保留”的 ASIN
                keep_df = edited_df[edited_df['决策'] == "保留"]
                
                # 按照 (原始行, 折扣) 进行聚合
                # 目的：同一行且折扣一样的 ASIN 放在一起；折扣变了的拆成新行
                grouped = keep_df.groupby(['原始行号', '拟提报折扣'])
                
                current_excel_row = 10
                
                # 找到 ASIN 列表列和折扣列的索引 (1-based)
                asin_col_idx = 1 # 假设第一列是 ASIN 列表
                disc_col_idx = 3 # 默认第三列
                for i, h in enumerate(original_headers, 1):
                    if h and 'ASIN' in str(h): asin_col_idx = i
                    if h and '折扣' in str(h) and '数值' in str(h): disc_col_idx = i

                for (orig_row_idx, disc), group in grouped:
                    new_asin_str = ";".join(group['ASIN'].tolist())
                    
                    # 如果当前行 <= 原表范围，直接修改原行
                    # 如果超出了，我们需要“克隆”原行的格式到新行
                    target_row = current_excel_row
                    source_row = orig_row_idx
                    
                    # 复制整行数据和格式
                    for col_idx in range(1, len(original_headers) + 1):
                        source_cell = ws_out.cell(row=source_row, column=col_idx)
                        new_cell = ws_out.cell(row=target_row, column=col_idx)
                        
                        # 复制内容
                        new_cell.value = source_cell.value
                        # 复制样式
                        if source_cell.has_style:
                            new_cell.font = copy(source_cell.font)
                            new_cell.border = copy(source_cell.border)
                            new_cell.fill = copy(source_cell.fill)
                            new_cell.number_format = copy(source_cell.number_format)
                            new_cell.alignment = copy(source_cell.alignment)

                    # 更新 ASIN 列表和折扣
                    ws_out.cell(row=target_row, column=asin_col_idx).value = new_asin_str
                    ws_out.cell(row=target_row, column=disc_col_idx).value = disc
                    
                    current_excel_row += 1

                # C. 清理多余的行（如果之前的行比现在多）
                if ws_out.max_row >= current_excel_row:
                    ws_out.delete_rows(current_excel_row, ws_out.max_row - current_excel_row + 1)

                # 保存
                out_io = io.BytesIO()
                wb_out.save(out_io)
                
                st.success(f"导出成功！保留了正确 ASIN 并修复了报错 ASIN。共生成 {current_excel_row-10} 行优惠券。")
                st.download_button("📥 下载无损格式修复表", out_io.getvalue(), "Amazon_Coupon_Fixed_OriginalFormat.xlsx")

if __name__ == "__main__":
    main()
