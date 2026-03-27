import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl
from copy import copy

st.set_page_config(page_title="Amazon Coupon 格式无损修复工具", layout="wide")

# --- 1. 核心解析逻辑：从批注文本中提取 ASIN、报错原因和净价格 ---
def parse_error_from_comment(comment_text):
    error_map = {}
    if not comment_text: return error_map
    
    # 逻辑：匹配 10位ASIN + 换行 + 紧接着的内容
    # 正则拆分：以 ASIN 为界
    blocks = re.split(r'([A-Z0-9]{10})\n', str(comment_text))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            
            # 提取“要求的净价格”
            req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            
            # 提取“报错原因”：取第一行或“要求的净价格”之前的描述
            # 常见格式：商品未通过优惠券定价验证。提高优惠券折扣以符合此优惠券的要求。
            reason = content.split('要求的净价格')[0].strip().replace('\n', ' ')
            
            error_map[asin] = {"req_price": req_price, "reason": reason}
    else:
        # 兜底单行处理
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', str(comment_text))
        req_price = float(req_price_match.group(1)) if req_price_match else None
        reason = str(comment_text).split('要求的净价格')[0].strip()
        error_map["GLOBAL"] = {"req_price": req_price, "reason": reason}
    return error_map

# --- 2. 无损读取函数 ---
def load_data_for_ui(file):
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active
    data = []
    headers = [cell.value for cell in ws[7]]
    for row_idx, row in enumerate(ws.iter_rows(min_row=10), 10):
        row_values = [cell.value for cell in row]
        if not any(row_values): continue
        last_cell = row[-1]
        comment_text = last_cell.comment.text if last_cell.comment else ""
        row_dict = {headers[i]: val for i, val in enumerate(row_values) if i < len(headers)}
        row_dict['_comment_error'] = comment_text
        row_dict['_orig_row_idx'] = row_idx
        data.append(row_dict)
    return pd.DataFrame(data), headers

# --- 3. Listing 读取 ---
def load_listing(file):
    if file is None: return None
    for enc in ['utf-8', 'utf-16', 'gbk', 'utf-8-sig']:
        try:
            file.seek(0)
            if file.name.endswith('.txt'):
                return pd.read_csv(file, sep='\t', encoding=enc)
            else:
                return pd.read_excel(file)
        except: continue
    return None

def main():
    st.title("🎯 Amazon Coupon 精准修复台")
    st.info("说明：系统会保留正确 ASIN，并根据批注提取报错原因及建议折扣。导出将维持原文件所有格式。")

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传带【批注】的报错模板", type=['xlsx'])

    if l_file and e_file:
        df_l = load_listing(l_file)
        e_file.seek(0)
        df_ui, original_headers = load_data_for_ui(e_file)

        if df_l is not None and df_ui is not None:
            df_l.columns = [c.lower() for c in df_l.columns]
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
            e_asin_list_col = next((c for c in df_ui.columns if 'ASIN' in str(c)), df_ui.columns[0])
            e_disc_col = next((c for c in df_ui.columns if '折扣' in str(c) and '数值' in str(c)), None)

            # --- 平铺 ASIN 准备决策 ---
            rows = []
            for _, row in df_ui.iterrows():
                raw_asin_str = str(row.get(e_asin_list_col, ""))
                asins = [a.strip() for a in raw_asin_str.replace(',', ';').split(';') if a.strip()]
                err_map = parse_error_from_comment(row.get('_comment_error', ""))
                
                for a in asins:
                    p_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    origin_price = p_match[0] if len(p_match) > 0 else None
                    
                    is_bad = a in err_map or ("GLOBAL" in err_map and len(asins) == 1)
                    info = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    # 折扣换算
                    current_disc = row.get(e_disc_col, 0.05)
                    suggested = current_disc
                    if is_bad and origin_price and info.get('req_price'):
                        needed = math.ceil(((float(origin_price) - float(info.get('req_price'))) / float(origin_price)) * 100)
                        suggested = needed / 100 if current_disc < 1 else needed

                    rows.append({
                        "决策": "保留",
                        "ASIN": a,
                        "状态": "❌ 批注报错" if is_bad else "✅ 正常",
                        "报错原因": info.get('reason', "-"), # 新增列
                        "拟提报折扣": suggested,
                        "原价": origin_price,
                        "要求净价": info.get('req_price'),
                        "原始行号": row.get('_orig_row_idx'),
                        "meta": row.to_dict()
                    })

            df_work = pd.DataFrame(rows)

            # --- 决策台渲染 ---
            st.subheader("🛠️ ASIN 修复决策台")
            edited_df = st.data_editor(
                df_work[['决策', 'ASIN', '状态', '报错原因', '拟提报折扣', '原价', '要求净价', '原始行号']],
                column_config={
                    "决策": st.column_config.SelectboxColumn("决策", options=["保留", "剔除"]),
                    "拟提报折扣": st.column_config.NumberColumn("拟提报折扣", format="%.2f"),
                    "报错原因": st.column_config.TextColumn("报错原因", width="large")
                },
                disabled=['ASIN', '状态', '报错原因', '原价', '要求净价', '原始行号'],
                hide_index=True,
                use_container_width=True
            )

            # --- 无损导出逻辑 ---
            if st.button("🚀 格式无损导出 (合并正确+保留项)"):
                e_file.seek(0)
                wb_out = openpyxl.load_workbook(e_file)
                ws_out = wb_out.active
                
                # 清空原数据区（10行以后）
                for r in range(10, ws_out.max_row + 1):
                    ws_out.cell(row=r, column=1).value = None 

                # 获取要保留的数据
                keep_df = edited_df[edited_df['决策'] == "保留"]
                # 按照原始行+折扣分组，确保不同折扣的剥离成新行
                grouped = keep_df.groupby(['原始行号', '拟提报折扣'])
                
                # 确定关键列索引
                asin_idx = 1
                disc_idx = 3
                for i, h in enumerate(original_headers, 1):
                    if h and 'ASIN' in str(h): asin_idx = i
                    if h and '折扣' in str(h) and '数值' in str(h): disc_idx = i

                current_row = 10
                for (orig_line, disc), group in grouped:
                    new_asin_str = ";".join(group['ASIN'].tolist())
                    
                    # 复制格式和元数据
                    for c in range(1, len(original_headers) + 1):
                        source_cell = ws_out.cell(row=orig_line, column=c)
                        target_cell = ws_out.cell(row=current_row, column=c)
                        target_cell.value = source_cell.value
                        if source_cell.has_style:
                            target_cell.font = copy(source_cell.font)
                            target_cell.border = copy(source_cell.border)
                            target_cell.fill = copy(source_cell.fill)
                            target_cell.number_format = copy(source_cell.number_format)
                            target_cell.alignment = copy(source_cell.alignment)
                    
                    # 覆盖 ASIN 列表和折扣
                    ws_out.cell(row=current_row, column=asin_idx).value = new_asin_str
                    ws_out.cell(row=current_row, column=disc_idx).value = disc
                    current_row += 1

                # 删除多余行
                if ws_out.max_row >= current_row:
                    ws_out.delete_rows(current_row, ws_out.max_row - current_row + 1)

                out_io = io.BytesIO()
                wb_out.save(out_io)
                st.success("导出成功！格式已完美还原。")
                st.download_button("📥 下载修复后的 Excel 文件", out_io.getvalue(), "Amazon_Fixed_Coupon.xlsx")

if __name__ == "__main__":
    main()
