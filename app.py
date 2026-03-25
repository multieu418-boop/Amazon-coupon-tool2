import streamlit as st
import pandas as pd
import re
import math
import io

# --- 页面基础配置 ---
st.set_page_config(page_title="Amazon Coupon 智能优化工具", layout="wide")

# --- 函数 1：解析亚马逊报错批注 (基于你提供的附件格式) ---
def parse_amazon_errors(error_text):
    """提取报错中的 ASIN、类型及要求的净价格"""
    results = []
    if not error_text.strip():
        return pd.DataFrame()
        
    # 正则匹配：10位大写字母数字组成的 ASIN
    blocks = re.split(r'([A-Z0-9]{10})\n', error_text)
    
    for i in range(1, len(blocks), 2):
        asin = blocks[i].strip()
        content = blocks[i+1]
        
        # 逻辑 A：无参考价
        if "没有经验证的参考价" in content or "没有经验证的历史售价" in content:
            results.append({
                "ASIN": asin, 
                "类型": "❌ 无参考价 (剔除)", 
                "要求净价": None,
                "原始报错": "无验证参考价"
            })
        
        # 逻辑 B：力度不足
        elif "要求的净价格" in content:
            # 提取金额数字
            price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(price_match.group(1)) if price_match else None
            results.append({
                "ASIN": asin, 
                "类型": "⚠️ 力度不足 (需增加)", 
                "要求净价": req_price,
                "原始报错": "调价提报"
            })
    return pd.DataFrame(results)

# --- 函数 2：多格式 & 智能列名识别 ---
def load_listing_data(uploaded_file):
    """适配多种格式，并自动寻找 ASIN 和 Price 列"""
    fname = uploaded_file.name.lower()
    df = None
    try:
        if fname.endswith('.txt'):
            df = pd.read_csv(uploaded_file, sep='\t', encoding='utf-16')
        elif fname.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        elif fname.endswith('.xls') or fname.endswith('.xlsx'):
            df = pd.read_excel(uploaded_file)
        
        if df is not None:
            # 1. 清理列名：转小写，去空格
            original_cols = df.columns
            clean_cols = [str(c).lower().strip() for c in original_cols]
            df.columns = clean_cols
            
            # 2. 模糊匹配 ASIN 列 (匹配 asin, asin1, asin2 等)
            asin_col = next((c for c in clean_cols if 'asin' in c), None)
            # 3. 模糊匹配 Price 列
            price_col = next((c for c in clean_cols if 'price' in c), None)
            
            if not asin_col or not price_col:
                st.error(f"找不到关键列！当前列名有: {list(original_cols)}")
                return None
            
            # 统一重命名方便后续逻辑
            df = df.rename(columns={asin_col: 'asin_standard', price_col: 'price_standard'})
            # 确保价格是数值型
            df['price_standard'] = pd.to_numeric(df['price_standard'], errors='coerce')
            return df
            
    except Exception as e:
        st.error(f"文件读取失败: {e}")
        return None

# --- 函数 3：核心计算逻辑 ---
def calculate_discount(row):
    if "无参考价" in str(row['类型']):
        return "直接剔除", 0
    
    if pd.notnull(row['price_standard']) and pd.notnull(row['要求净价']):
        # 计算百分比并向上取整
        diff = row['price_standard'] - row['要求净价']
        raw_pct = (diff / row['price_standard']) * 100
        final_pct = math.ceil(raw_pct) 
        
        # 亚马逊最低门槛 5%
        if final_pct < 5: final_pct = 5
        return f"建议 {final_pct}%", final_pct
        
    return "缺失原价", 0

# --- 主程序 ---
def main():
    st.title("🎯 Amazon Coupon 第二阶段 (智能列名适配版)")
    
    col_l, col_r = st.columns(2)
    with col_l:
        listing_file = st.file_uploader("1. 上传 All Listing (支持所有格式)", type=['txt', 'xls', 'xlsx', 'csv'])
    with col_r:
        error_input = st.text_area("2. 粘贴亚马逊报错内容", height=150)

    if listing_file and error_input:
        df_listing = load_listing_data(listing_file)
        df_errors = parse_amazon_errors(error_input)
        
        if df_listing is not None and not df_errors.empty:
            # 数据关联
            df_merge = pd.merge(df_errors, df_listing[['asin_standard', 'price_standard']], 
                                left_on='ASIN', right_on='asin_standard', how='left')
            
            # 计算建议
            df_merge['系统结论'], df_merge['建议力度'] = zip(*df_merge.apply(calculate_discount, axis=1))

            st.subheader("📊 报错 ASIN 智能分析")
            
            # 增加快速筛选
            status_filter = st.radio("筛选状态：", ["全部", "仅看力度不足", "仅看无参考价"], horizontal=True)
            if status_filter == "仅看力度不足":
                df_merge = df_merge[df_merge['类型'].str.contains("力度不足")]
            elif status_filter == "仅看无参考价":
                df_merge = df_merge[df_merge['类型'].str.contains("无参考价")]

            # 可编辑表格
            edited_df = st.data_editor(
                df_merge[['ASIN', 'price_standard', '要求净价', '类型', '系统结论', '建议力度']],
                column_config={
                    "price_standard": "All Listing原价",
                    "要求净价": "亚马逊要求价",
                    "建议力度": st.column_config.NumberColumn("拟提报折扣%", format="%d%%"),
                    "保留": st.column_config.CheckboxColumn("是否保留?", default=False)
                },
                disabled=["ASIN", "price_standard", "要求净价", "类型", "系统结论"],
                hide_index=True
            )

            # 导出与归纳
            st.divider()
            if st.button("🚀 执行归纳并生成下载文件"):
                # 只保留有力度的 ASIN
                valid_df = edited_df[edited_df['建议力度'] >= 5]
                
                if not valid_df.empty:
                    # 核心归纳逻辑：按折扣百分比合并 ASIN
                    final_grouped = valid_df.groupby('建议力度')['ASIN'].apply(lambda x: ';'.join(list(set(x)))).reset_index()
                    
                    st.write("### 归纳结果 (已合并相同折扣)")
                    st.dataframe(final_grouped, use_container_width=True)
                    
                    # 生成 Excel 下载
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        # 构造亚马逊上传模板所需的简单格式
                        final_grouped.columns = ['Discount_Percentage', 'ASIN_List']
                        final_grouped.to_excel(writer, index=False, sheet_name='Fixed_Coupons')
                    
                    st.download_button(
                        label="📥 下载提报模板",
                        data=output.getvalue(),
                        file_name="Amazon_Fixed_Coupon.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.error("没有可提报的有效 ASIN。")

if __name__ == "__main__":
    main()
