import streamlit as st
import pandas as pd
import re
import math
import io
import openpyxl

st.set_page_config(page_title="Amazon Coupon 批注剥离系统", layout="wide")

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

# --- 2. 带批注的 Excel 读取 ---
def load_template_with_comments(file):
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        data = []
        # 第7行表头
        headers = [str(cell.value).strip() if cell.value else f"Col{i}" for i, cell in enumerate(ws[7], 1)]
        # 从第10行开始数据
        for row in ws.iter_rows(min_row=10):
            row_values = [cell.value for cell in row]
            if not any(row_values): continue
            # 提取最后一列批注
            last_cell = row[-1]
            comment_text = last_cell.comment.text if last_cell.comment else ""
            row_dict = dict(zip(headers, row_values))
            row_dict['_comment_error'] = comment_text
            data.append(row_dict)
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Excel 解析失败: {e}")
        return None

# --- 3. 样式标记：高折扣预警 ---
def highlight_high_discount(s):
    # 如果建议折扣 > 30% (即 0.3)，背景标黄/文字标红
    return ['background-color: #ffcccc' if (isinstance(val, (int, float)) and val > 0.3) else '' for val in s]

def main():
    st.title("🎯 Amazon Coupon 批注剥离与高损耗预警系统")
    st.sidebar.header("⚙️ 设置")
    threshold = st.sidebar.slider("高折扣预警阈值 (%)", 5, 50, 30) / 100

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传带【批注】报错的提报模板", type=['xlsx'])

    if l_file and e_file:
        # 读取 Listing
        df_l = None
        for enc in ['utf-8', 'utf-16', 'gbk']:
            try:
                l_file.seek(0)
                df_l = pd.read_csv(l_file, sep='\t', encoding=enc) if l_file.name.endswith('.txt') else pd.read_excel(l_file)
                break
            except: continue
        
        df_e = load_template_with_comments(e_file)

        if df_l is not None and df_e is not None:
            df_l.columns = [c.lower() for c in df_l.columns]
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c or '价格' in c), None)
            e_asin_list_col = next((c for c in df_e.columns if 'ASIN' in c), df_e.columns[0])
            e_disc_col = next((c for c in df_e.columns if '折扣' in c and '数值' in c), None)

            # --- 铺开数据 ---
            rows = []
            for idx, row in df_e.iterrows():
                asins = [a.strip() for a in str(row.get(e_asin_list_col, "")).replace(',', ';').split(';') if a.strip()]
                err_map = parse_error_from_comment(row.get('_comment_error', ""))
                for a in asins:
                    p_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    origin_price = p_match[0] if len(p_match) > 0 else None
                    is_bad = a in err_map or ("GLOBAL" in err_map and len(asins) == 1)
                    info = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    rows.append({
                        "原始行": idx + 10,
                        "ASIN": a,
                        "状态": "❌ 批注报错" if is_bad else "✅ 正常",
                        "Listing原价": origin_price,
                        "要求净价": info.get('req_price'),
                        "当前折扣": row.get(e_disc_col),
                        "批注原文": info.get('msg', "无报错"),
                        "meta": row.to_dict()
                    })

            df_work = pd.DataFrame(rows)

            def calc_disc(r):
                if r['状态'] == "✅ 正常": return r['当前折扣']
                if pd.notnull(r['Listing原价']) and pd.notnull(r['要求净价']):
                    needed = math.ceil(((float(r['Listing原价']) - float(r['要求净价'])) / float(r['Listing原价'])) * 100)
                    return needed / 100 if r['当前折扣'] < 1 else needed
                return 0

            df_work['建议折扣'] = df_work.apply(calc_disc, axis=1)
            df_work['决策'] = df_work['建议折扣'].apply(lambda x: "保留并修复" if x > 0 else "剔除")

            # --- 决策台显示 ---
            st.subheader("🛠️ 批注修复决策台 (高折扣已标红)")
            
            # 使用 Style 渲染表格
            styled_df = df_work[['决策', 'ASIN', '状态', '建议折扣', 'Listing原价', '要求净价', '批注原文', '原始行']].style.apply(highlight_high_discount, subset=['建议折扣'])
            
            edited_df = st.data_editor(
                df_work[['决策', 'ASIN', '状态', '建议折扣', 'Listing原价', '要求净价', '批注原文', '原始行']],
                column_config={
                    "建议折扣": st.column_config.NumberColumn(f"拟用折扣 (>{threshold*100}%慎重)", format="%.2f")
                },
                disabled=['ASIN', '状态', 'Listing原价', '要求净价', '批注原文', '原始行'],
                hide_index=True,
                use_container_width=True
            )

            # 侧边栏实时汇总
            high_risk_count = (edited_df['建议折扣'] > threshold).sum()
            if high_risk_count > 0:
                st.sidebar.error(f"⚠️ 警告：检测到 {high_risk_count} 个 ASIN 折扣力度过大！")

            # --- 导出 ---
            if st.button("🚀 导出修复后的提报单"):
                keep = edited_df[edited_df['决策'] == "保留并修复"]
                if not keep.empty:
                    final_rows = []
                    # 剥离逻辑：原始行+折扣一致的合并
                    for (orig_idx, disc), group in keep.groupby(['原始行', '建议折扣']):
                        meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['meta'].iloc[0]
                        new_row = {k: v for k, v in meta.items() if not k.startswith('_')}
                        new_row[e_asin_list_col] = ";".join(group['ASIN'].tolist())
                        new_row[e_disc_col] = disc
                        final_rows.append(new_row)
                    
                    res_df = pd.DataFrame(final_rows)
                    st.success("导出成功！已将报错 ASIN 剥离为独立行。")
                    st.dataframe(res_df)
                    
                    out = io.BytesIO()
                    with pd.ExcelWriter(out, engine='openpyxl') as writer:
                        res_df.to_excel(writer, index=False)
                    st.download_button("📥 下载修复文件", out.getvalue(), "Coupon_Stripped_Fixed.xlsx")

if __name__ == "__main__":
    main()
