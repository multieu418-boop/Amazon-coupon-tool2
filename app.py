import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 智能剥离工具", layout="wide")

# --- 1. 核心解析逻辑：从 N 列（或最后一列）抓取报错 ASIN 和 要求的净价格 ---
def parse_amazon_error_column(error_msg):
    """
    逻辑：解析如 'B0G8WMW65L\n商品未通过优惠券定价验证。要求的净价格：€37.99' 的文本
    """
    error_map = {}
    if pd.isna(error_msg) or str(error_msg).strip() == "": 
        return error_map
    
    # 匹配 10位ASIN + 任意文字 + 要求的净价格
    # 正则：匹配 ASIN，后面跟着直到下一个 ASIN 或结尾的内容
    blocks = re.split(r'([A-Z0-9]{10})\n', str(error_msg))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            # 提取数字（支持 €, $, ￡ 等符号后的数字）
            req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            error_map[asin] = {"req_price": req_price, "msg": content.strip()}
    else:
        # 兜底：如果一整行只有一个 ASIN 报错且没换行
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', str(error_msg))
        req_price = float(req_price_match.group(1)) if req_price_match else None
        error_map["GLOBAL"] = {"req_price": req_price, "msg": str(error_msg)}
    return error_map

# --- 2. 智能读取：跳过前6行，第7行表头，跳过8-9行举例 ---
def load_coupon_template(file):
    if file is None: return None
    try:
        # 针对你上传的 CSV/Excel 结构：header 是第 7 行 (index 6)
        # 先读入，再切掉前两行数据（举例行）
        df = pd.read_excel(file, header=6) if file.name.endswith('.xlsx') else pd.read_csv(file, header=6, encoding='utf-8-sig')
        df = df.iloc[2:].reset_index(drop=True) 
        return df.dropna(how='all', subset=[df.columns[0]]) # 确保不读空行
    except Exception as e:
        st.error(f"读取模板失败: {e}")
        return None

# --- 3. ALL LISTING 读取 (支持 TXT/Excel) ---
def load_listing(file):
    if file is None: return None
    try:
        if file.name.endswith('.txt'):
            df = pd.read_csv(file, sep='\t', encoding='utf-16') # 亚马逊导出的txt通常是utf-16
        else:
            df = pd.read_excel(file)
        # 统一列名
        df.columns = [str(c).strip().lower() for c in df.columns]
        return df
    except:
        file.seek(0)
        return pd.read_csv(file, sep='\t', encoding='gbk')

def main():
    st.title("🎯 Amazon Coupon 报错自动修复 (文件版)")
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        l_file = st.file_uploader("1. 上传 All Listing 报告 (匹配原价)", type=['txt', 'xlsx', 'csv'])
    with col2:
        e_file = st.file_uploader("2. 上传带报错的提报模板 (读取N列报错)", type=['xlsx', 'csv'])

    if l_file and e_file:
        df_l = load_listing(l_file)
        df_e = load_coupon_template(e_file)

        if df_l is not None and df_e is not None:
            # 定位 Listing 的 ASIN 和 Price 列
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)
            
            # 定位 模板 的 ASIN列 和 备注/报错列 (通常在最后或叫“错误”)
            e_asin_list_col = df_e.columns[0] # 第一列通常是 ASIN 列表
            e_error_col = df_e.columns[-1]    # 最后一列（N列）通常是错误原因
            e_disc_col = next((c for c in df_e.columns if '折扣' in c and '数值' in c), df_e.columns[2])

            # --- 核心数据铺开 (Flattening) ---
            flattened = []
            for idx, row in df_e.iterrows():
                raw_asins = str(row.get(e_asin_list_col, ""))
                if not raw_asins or raw_asins == "nan": continue
                
                # 关键：从 N 列识别哪些 ASIN 报错了，要求多少钱
                err_map = parse_amazon_error_column(str(row.get(e_error_col, "")))
                asin_list = [a.strip() for a in re.split(r'[;,\s]+', raw_asins) if a.strip()]
                
                for a in asin_list:
                    # 1. 匹配 ALL LISTING 中的原价
                    price_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    origin_price = price_match[0] if len(price_match) > 0 else None
                    
                    # 2. 判断是否属于报错 ASIN
                    is_bad = a in err_map or ("GLOBAL" in err_map and len(asin_list) == 1)
                    info = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    flattened.append({
                        "原始行": idx + 10, # 方便你对表
                        "ASIN": a,
                        "状态": "❌ 报错" if is_bad else "✅ 正常",
                        "Listing原价": origin_price,
                        "要求净价": info.get('req_price'),
                        "当前折扣": row.get(e_disc_col),
                        "报错详情": info.get('msg', "正常"),
                        "原始Row": row.to_dict()
                    })

            df_work = pd.DataFrame(flattened)

            # --- 3. 根据报错价格自动计算建议折扣 ---
            def calculate_new_disc(r):
                if r['状态'] == "✅ 正常": return r['当前折扣']
                if pd.notnull(r['Listing原价']) and pd.notnull(r['要求净价']):
                    # 计算公式：(原价 - 净价) / 原价
                    new_val = math.ceil(((r['Listing原价'] - r['要求净价']) / r['Listing原价']) * 100)
                    # 亚马逊折扣通常是 0.18 这种格式或 18 这种整数，这里保持与原表一致
                    return new_val / 100 if r['当前折扣'] < 1 else new_val
                return 0

            df_work['建议折扣'] = df_work.apply(calculate_new_disc, axis=1)
            df_work['决策'] = df_work['建议折扣'].apply(lambda x: "保留并修复" if x > 0 else "剔除")

            # --- 4. 决策台 ---
            st.subheader("🛠️ ASIN 修复决策台")
            st.write("系统已自动匹配原价并根据报错金额倒推了折扣。请检查“建议折扣”：")
            
            final_df = st.data_editor(
                df_work[['决策', 'ASIN', '状态', '建议折扣', 'Listing原价', '要求净价', '报错详情', '原始行']],
                column_config={
                    "决策": st.column_config.SelectboxColumn("操作", options=["保留并修复", "剔除"]),
                    "建议折扣": st.column_config.NumberColumn("拟用新折扣", format="%.2f")
                },
                disabled=['ASIN', '状态', 'Listing原价', '要求净价', '报错详情', '原始行'],
                hide_index=True,
                use_container_width=True
            )

            # --- 5. 归类重组输出 ---
            if st.button("🚀 导出：将报错 ASIN 剥离成新 Coupon"):
                keep = final_df[final_df['决策'] == "保留并修复"]
                if keep.empty:
                    st.warning("没有可导出的 ASIN")
                else:
                    output_rows = []
                    # 逻辑：同一行原始数据，如果折扣一致则合并；折扣变了则分行
                    for (orig_idx, disc), group in keep.groupby(['原始行', '建议折扣']):
                        meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['原始Row'].iloc[0]
                        new_row = meta.copy()
                        new_row[e_asin_list_col] = ";".join(group['ASIN'].tolist())
                        new_row[e_disc_col] = disc
                        # 自动在名称后加标记
                        name_key = next((c for c in df_e.columns if '名称' in c), None)
                        if name_key: new_row[name_key] = f"FIX-{new_row[name_key]}"
                        
                        output_rows.append(new_row)
                    
                    res_df = pd.DataFrame(output_rows)
                    st.write("### 提报文件预览")
                    st.dataframe(res_df)
                    
                    out_io = io.BytesIO()
                    with pd.ExcelWriter(out_io, engine='openpyxl') as writer:
                        res_df.to_excel(writer, index=False)
                    st.download_button("📥 下载修复后的提报文件", out_io.getvalue(), "Amazon_Fixed_Coupon.xlsx")

if __name__ == "__main__":
    main()
