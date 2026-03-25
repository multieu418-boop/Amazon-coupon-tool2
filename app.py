import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 智能剥离工具", layout="wide")

# --- 1. 核心解析逻辑：从 N 列抓取报错 ASIN 和 要求的净价格 ---
def parse_amazon_error_column(error_msg):
    error_map = {}
    if pd.isna(error_msg) or str(error_msg).strip() == "": 
        return error_map
    
    # 匹配 10位ASIN + 换行 + 报错内容（含要求的净价格）
    # 使用正则表达式分割每个 ASIN 段落
    blocks = re.split(r'([A-Z0-9]{10})\n', str(error_msg))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            # 提取数字（支持各种货币符号）
            req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            error_map[asin] = {"req_price": req_price, "msg": content.strip()}
    else:
        # 兜底：整行单条报错
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', str(error_msg))
        req_price = float(req_price_match.group(1)) if req_price_match else None
        error_map["GLOBAL"] = {"req_price": req_price, "msg": str(error_msg)}
    return error_map

# --- 2. 强力读取函数 (解决 UnicodeDecodeError) ---
def smart_read_file(file, is_template=False):
    if file is None: return None
    fname = file.name.lower()
    content = file.read()
    
    # 尝试多种编码
    for enc in ['utf-8', 'utf-16', 'gbk', 'utf-8-sig', 'cp1252']:
        try:
            if fname.endswith('.xlsx'):
                df = pd.read_excel(io.BytesIO(content), header=6 if is_template else 0)
            elif fname.endswith('.txt'):
                df = pd.read_csv(io.BytesIO(content), sep='\t', encoding=enc)
            else: # csv
                df = pd.read_csv(io.BytesIO(content), encoding=enc, header=6 if is_template else 0)
            
            if is_template:
                # 针对模板：跳过 8-9 行举例行
                df = df.iloc[2:].reset_index(drop=True)
            
            # 清理列名
            df.columns = [str(c).strip() for c in df.columns]
            return df.dropna(how='all', subset=[df.columns[0]])
        except Exception:
            continue
    st.error(f"文件 {fname} 编码无法识别，请尝试另存为 Excel 后上传。")
    return None

def main():
    st.title("🎯 Amazon Coupon 精准修复系统")
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 报告", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传带报错的提报模板", type=['xlsx', 'csv'])

    if l_file and e_file:
        df_l = smart_read_file(l_file, is_template=False)
        df_e = smart_read_file(e_file, is_template=True)

        if df_l is not None and df_e is not None:
            # 标准化 Listing 列名以供查找
            df_l.columns = [c.lower() for c in df_l.columns]
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)
            
            # 模板列名定位 (N列通常是最后一列)
            e_asin_list_col = df_e.columns[0]
            e_error_col = df_e.columns[-1]
            e_disc_col = next((c for c in df_e.columns if '折扣' in c and '数值' in c), df_e.columns[2])

            # --- 核心逻辑：拆解 ASIN 并匹配原价 ---
            flattened = []
            for idx, row in df_e.iterrows():
                raw_asins = str(row.get(e_asin_list_col, ""))
                if not raw_asins or raw_asins == "nan": continue
                
                err_map = parse_amazon_error_column(str(row.get(e_error_col, "")))
                asin_list = [a.strip() for a in re.split(r'[;,\s]+', raw_asins) if a.strip()]
                
                for a in asin_list:
                    # 查找原价
                    p_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    origin_price = p_match[0] if len(p_match) > 0 else None
                    
                    is_bad = a in err_map or ("GLOBAL" in err_map and len(asin_list) == 1)
                    info = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    flattened.append({
                        "原始行": idx + 10,
                        "ASIN": a,
                        "状态": "❌ 报错" if is_bad else "✅ 正常",
                        "Listing原价": origin_price,
                        "要求净价": info.get('req_price'),
                        "当前折扣": row.get(e_disc_col),
                        "报错详情": info.get('msg', "正常"),
                        "原始Row": row.to_dict()
                    })

            df_work = pd.DataFrame(flattened)

            # 自动计算逻辑
            def calculate_new_disc(r):
                if r['状态'] == "✅ 正常": return r['当前折扣']
                if pd.notnull(r['Listing原价']) and pd.notnull(r['要求净价']):
                    new_val = math.ceil(((r['Listing原价'] - r['要求净价']) / r['Listing原价']) * 100)
                    return new_val / 100 if r['当前折扣'] < 1 else new_val
                return 0

            df_work['建议折扣'] = df_work.apply(calculate_new_disc, axis=1)
            df_work['操作决策'] = df_work['建议折扣'].apply(lambda x: "保留并修复" if x > 0 else "剔除")

            # --- 3. 展现决策台 ---
            st.subheader("🛠️ ASIN 修复决策台")
            edited_df = st.data_editor(
                df_work[['操作决策', 'ASIN', '状态', '建议折扣', 'Listing原价', '要求净价', '报错详情', '原始行']],
                column_config={
                    "操作决策": st.column_config.SelectboxColumn("决策", options=["保留并修复", "剔除"]),
                    "建议折扣": st.column_config.NumberColumn("拟用折扣", format="%.2f")
                },
                disabled=['ASIN', '状态', 'Listing原价', '要求净价', '报错详情', '原始行'],
                hide_index=True,
                use_container_width=True
            )

            # --- 4. 导出逻辑 ---
            if st.button("🚀 生成并下载修复后的提报文件"):
                keep = edited_df[edited_df['操作决策'] == "保留并修复"]
                if keep.empty:
                    st.warning("无 ASIN 保留")
                else:
                    output_rows = []
                    # 按原始行和折扣分组（剥离逻辑）
                    for (orig_idx, disc), group in keep.groupby(['原始行', '建议折扣']):
                        meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['原始Row'].iloc[0]
                        new_row = meta.copy()
                        new_row[e_asin_list_col] = ";".join(group['ASIN'].tolist())
                        new_row[e_disc_col] = disc
                        output_rows.append(new_row)
                    
                    res_df = pd.DataFrame(output_rows)[df_e.columns]
                    st.dataframe(res_df)
                    
                    out_io = io.BytesIO()
                    with pd.ExcelWriter(out_io, engine='openpyxl') as writer:
                        res_df.to_excel(writer, index=False)
                    st.download_button("📥 下载文件", out_io.getvalue(), "Amazon_Fixed.xlsx")

if __name__ == "__main__":
    main()
