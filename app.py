import streamlit as st
import pandas as pd
import re
import math
import io

st.set_page_config(page_title="Amazon Coupon 智能剥离工具", layout="wide")

# --- 1. 智能解析报错详情 (解析要求的净价格) ---
def parse_error_details(error_msg):
    error_map = {}
    if pd.isna(error_msg) or str(error_msg).strip() == "": 
        return error_map
    
    # 匹配 ASIN(10位) + 报错内容
    blocks = re.split(r'([A-Z0-9]{10})\n', str(error_msg))
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            asin = blocks[i].strip()
            content = blocks[i+1]
            req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(req_price_match.group(1)) if req_price_match else None
            error_map[asin] = {"req_price": req_price, "msg": content.strip()}
    else:
        # 兜底：如果整行只有一个 ASIN 报错
        req_price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', str(error_msg))
        req_price = float(req_price_match.group(1)) if req_price_match else None
        error_map["GLOBAL"] = {"req_price": req_price, "msg": str(error_msg)}
    return error_map

# --- 2. 亚马逊模板读取 (跳过说明和举例) ---
def load_amazon_template(file):
    if file is None: return None
    try:
        # 第7行是表头 (header=6)
        df = pd.read_excel(file, header=6)
        # 跳过第8-9行举例 (即 index 0 和 1)
        df = df.iloc[2:].reset_index(drop=True)
        # 清理列名
        df.columns = [str(c).strip() for c in df.columns]
        return df.dropna(how='all')
    except Exception as e:
        # 如果是 CSV 格式
        file.seek(0)
        df = pd.read_csv(file, header=6, encoding='utf-8-sig')
        df = df.iloc[2:].reset_index(drop=True)
        df.columns = [str(c).strip() for c in df.columns]
        return df.dropna(how='all')

# --- 3. Listing 文件读取 ---
def load_listing_file(file):
    if file is None: return None
    try:
        fname = file.name.lower()
        if fname.endswith('.txt'):
            df = pd.read_csv(file, sep='\t', encoding='utf-8')
        else:
            df = pd.read_excel(file)
        df.columns = [str(c).strip().lower() for c in df.columns]
        return df
    except:
        file.seek(0)
        return pd.read_csv(file, sep='\t', encoding='gbk')

def main():
    st.title("🎯 Amazon Coupon ASIN 级精准修复")
    st.info("逻辑：将报错 ASIN 从原 Coupon 剥离。正常 ASIN 原样保留，报错 ASIN 修复折扣后单独生成新 Coupon。")

    c1, c2 = st.columns(2)
    with c1:
        l_file = st.file_uploader("1. 上传 All Listing 表", type=['txt', 'xlsx', 'csv'])
    with c2:
        e_file = st.file_uploader("2. 上传亚马逊报错模板", type=['xlsx', 'csv'])

    if l_file and e_file:
        df_l = load_listing_file(l_file)
        df_e = load_amazon_template(e_file)

        if df_l is not None and df_e is not None:
            # 自动定位关键列
            l_asin_col = next((c for c in df_l.columns if 'asin' in c), None)
            l_price_col = next((c for c in df_l.columns if 'price' in c), None)
            
            # 匹配表头（兼容中文/英文）
            asin_list_col = next((c for c in df_e.columns if 'ASIN 列表' in c or 'ASIN List' in c), df_e.columns[0])
            error_col = next((c for c in df_e.columns if '错误' in c or 'Error' in c or '结果' in c), df_e.columns[-1])
            discount_col = next((c for c in df_e.columns if '折扣' in c and '数值' in c or 'Discount' in c), None)

            # --- 拆解：将一行多个 ASIN 铺开 ---
            flattened_data = []
            for idx, row in df_e.iterrows():
                raw_asins = str(row.get(asin_list_col, ""))
                if not raw_asins or raw_asins == "nan": continue
                
                err_map = parse_error_details(str(row.get(error_col, "")))
                asin_items = [a.strip() for a in re.split(r'[;,\s]+', raw_asins) if a.strip()]
                
                for a in asin_items:
                    # 匹配 Listing 价格
                    p_match = df_l[df_l[l_asin_col] == a][l_price_col].values if l_asin_col else []
                    price = p_match[0] if len(p_match) > 0 else None
                    
                    # 检查此 ASIN 是否报错
                    is_err = a in err_map or (len(asin_items) == 1 and "GLOBAL" in err_map)
                    err_info = err_map.get(a, err_map.get("GLOBAL", {}))
                    
                    flattened_data.append({
                        "原始行号": idx + 10, # 估算行号方便对照
                        "ASIN": a,
                        "报错状态": "❌ 报错" if is_err else "✅ 正常",
                        "原价": price,
                        "要求净价": err_info.get('req_price'),
                        "当前折扣": row.get(discount_col, 0.05),
                        "报错信息": err_info.get('msg', "正常"),
                        "原始Row": row.to_dict() # 保存整行数据用于还原
                    })

            df_work = pd.DataFrame(flattened_data)

            # 自动计算建议折扣
            def suggest_disc(r):
                if r['报错状态'] == "✅ 正常": return r['当前折扣']
                if r['要求净价'] and r['原价']:
                    # 换算百分比 (1 - 净价/原价)
                    calc = math.ceil(((r['原价'] - r['要求净价']) / r['原价']) * 100)
                    return calc / 100 if r['当前折扣'] < 1 else calc
                return 0

            df_work['修复后折扣'] = df_work.apply(suggest_disc, axis=1)
            df_work['操作选择'] = df_work['修复后折扣'].apply(lambda x: "保留提报" if x > 0 else "剔除")

            # --- 交互界面 ---
            st.subheader("🛠️ ASIN 筛选与折扣确认")
            st.warning("请在下表中确认：哪些 ASIN 需要修复保留，哪些需要彻底剔除。")
            
            edited_df = st.data_editor(
                df_work[['操作选择', 'ASIN', '报错状态', '修复后折扣', '原价', '要求净价', '报错信息', '原始行号']],
                column_config={
                    "操作选择": st.column_config.SelectboxColumn("决策", options=["保留提报", "剔除"], width="medium"),
                    "修复后折扣": st.column_config.NumberColumn("拟用折扣", help="你可以手动微调这里的折扣")
                },
                disabled=['ASIN', '报错状态', '原价', '要求净价', '报错信息', '原始行号'],
                hide_index=True,
                use_container_width=True
            )

            # --- 导出：重新合并 ---
            if st.button("🚀 生成修复版提报文件 (剥离报错 ASIN)"):
                keep_df = edited_df[edited_df['操作选择'] == "保留提报"]
                if keep_df.empty:
                    st.error("没有勾选任何需要保留的 ASIN")
                else:
                    final_rows = []
                    # 按 (原始行号 + 修复后折扣) 分组
                    # 逻辑：同一行中，如果折扣变了，必须拆成新的 Coupon 行
                    for (orig_line, disc), group in keep_df.groupby(['原始行号', '修复后折扣']):
                        # 取回该行原始的元数据（日期、预算等）
                        meta = df_work[df_work['ASIN'] == group['ASIN'].iloc[0]]['原始Row'].iloc[0]
                        
                        new_row = meta.copy()
                        new_row[asin_list_col] = ";".join(group['ASIN'].tolist())
                        new_row[discount_col] = disc
                        # 自动标记一下名称，方便在亚马逊后台区分
                        name_col = next((c for c in df_e.columns if '名称' in c or 'Name' in c), None)
                        if name_col:
                            new_row[name_col] = f"FIXED-{new_row[name_col]}"
                        
                        final_rows.append(new_row)
                    
                    res_df = pd.DataFrame(final_rows)
                    # 恢复列顺序
                    res_df = res_df[df_e.columns]
                    
                    st.write("### 提报预览 (报错 ASIN 已独立成行)")
                    st.dataframe(res_df)
                    
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        res_df.to_excel(writer, index=False)
                    st.download_button("📥 下载修复后的提报文件", output.getvalue(), "Coupon_Stripped_Fixed.xlsx")

if __name__ == "__main__":
    main()
