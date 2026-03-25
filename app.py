import streamlit as st
import pandas as pd
import re
import math
import io

# --- 页面基础配置 ---
st.set_page_config(page_title="Amazon Coupon 智能优化助手", layout="wide")

# --- 函数 1：解析亚马逊报错批注 ---
def parse_amazon_errors(error_text):
    """提取 ASIN、类型及要求的净价格"""
    results = []
    if not error_text.strip():
        return pd.DataFrame()
        
    # 按 ASIN 模式（10位大写字母数字组合）分割块
    blocks = re.split(r'([A-Z0-9]{10})\n', error_text)
    
    for i in range(1, len(blocks), 2):
        asin = blocks[i].strip()
        content = blocks[i+1]
        
        if "没有经验证的参考价" in content or "没有经验证的历史售价" in content:
            results.append({
                "ASIN": asin, 
                "类型": "❌ 无参考价 (剔除)", 
                "要求净价": None,
                "原始报错": "ASIN 没有经验证的历史售价"
            })
        elif "要求的净价格" in content:
            price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(price_match.group(1)) if price_match else None
            results.append({
                "ASIN": asin, 
                "类型": "⚠️ 力度不足 (需增加)", 
                "要求净价": req_price,
                "原始报错": "提高优惠券折扣"
            })
    return pd.DataFrame(results)

# --- 函数 2：多格式 & 智能编码 & 智能列名识别 (核心修复部分) ---
def load_listing_data(uploaded_file):
    """自动尝试多种编码和格式读取文件"""
    fname = uploaded_file.name.lower()
    df = None
    
    try:
        if fname.endswith('.txt') or fname.endswith('.csv'):
            # 自动探测编码策略
            encodings = ['utf-8', 'utf-16', 'gbk', 'utf-8-sig']
            sep = '\t' if fname.endswith('.txt') else ','
            
            for enc in encodings:
                try:
                    uploaded_file.seek(0) # 每次尝试前重置文件指针
                    df = pd.read_csv(uploaded_file, sep=sep, encoding=enc)
                    if not df.empty and len(df.columns) > 1: # 简单判断是否读取成功
                        break
                except:
                    continue
        elif fname.endswith('.xls') or fname.endswith('.xlsx'):
            df = pd.read_excel(uploaded_file)
        
        if df is not None:
            # 1. 清理列名
            original_cols = df.columns
            clean_cols = [str(c).lower().strip() for c in original_cols]
            df.columns = clean_cols
            
            # 2. 模糊匹配关键列：ASIN 和 Price
            # 匹配 asin, asin1, asin2 等；匹配 price, price-amount 等
            asin_col = next((c for c in clean_cols if 'asin' in c), None)
            price_col = next((c for c in clean_cols if 'price' in c), None)
            
            if not asin_col or not price_col:
                st.warning(f"列名识别提示：未完全匹配。当前检测到列：{list(original_cols)}")
                # 备选方案：如果没找到包含 price 的，尝试找价格数值最多的列（此处简化处理）
                return None
            
            # 统一列名用于逻辑运算
            df = df.rename(columns={asin_col: 'asin_standard', price_col: 'price_standard'})
            df['price_standard'] = pd.to_numeric(df['price_standard'], errors='coerce')
            return df
            
    except Exception as e:
        st.error(f"无法解析该文件: {e}")
        return None

# --- 函数 3：计算逻辑 ---
def apply_calculation(row):
    if "无参考价" in str(row['类型']):
        return "直接剔除", 0
    
    if pd.notnull(row['price_standard']) and pd.notnull(row['要求净价']):
        # 计算建议折扣 = (原价 - 亚马逊要求价) / 原价
        diff = row['price_standard'] - row['要求净价']
        raw_pct = (diff / row['price_standard']) * 100
        final_pct = math.ceil(raw_pct) # 向上取整
        
        # 亚马逊 Coupon 门槛：5%-50%（通常建议不低于5%）
        if final_pct < 5: final_pct = 5
        return f"建议 {final_pct}%", final_pct
            
    return "缺失原价", 0

# --- 主程序 ---
def main():
    st.title("🎯 Amazon Coupon 智能修复系统 (全格式适配版)")
    st.info("提示：支持 TXT(BOM/无BOM)、CSV、Excel。自动识别 asin1/asin2 等列名。")

    col_l, col_r = st.columns(2)
    with col_l:
        listing_file = st.file_uploader("1. 上传 All Listing 文件", type=['txt', 'xls', 'xlsx', 'csv'])
    with col_r:
        error_input = st.text_area("2. 粘贴亚马逊 N 列批注报错", height=150)

    if listing_file and error_input:
        df_listing = load_listing_data(listing_file)
        if df_listing is None:
            st.error("无法读取 Listing 文件的 ASIN 或价格列，请确认文件内容是否正确。")
            return

        df_errors = parse_amazon_errors(error_input)
        if df_errors.empty:
            st.warning("未能从粘贴的内容中解析出报错 ASIN，请检查输入格式。")
            return

        # 数据合并与计算
        df_merge = pd.merge(df_errors, df_listing[['asin_standard', 'price_standard']], 
                            left_on='ASIN', right_on='asin_standard', how='left')
        
        df_merge['系统结论'], df_merge['建议力度'] = zip(*df_merge.apply(apply_calculation, axis=1))

        # 工作台界面
        st.subheader("📊 调价决策工作台")
        
        # 筛选器
        status_view = st.multiselect("筛选类型：", options=df_merge['类型'].unique(), default=df_merge['类型'].unique())
        display_df = df_merge[df_merge['类型'].isin(status_view)]

        edited_df = st.data_editor(
            display_df[['ASIN', 'price_standard', '要求净价', '类型', '系统结论', '建议力度']],
            column_config={
                "price_standard": "原价",
                "要求净价": "要求净价",
                "建议力度": st.column_config.NumberColumn("拟提报折扣%", format="%d%%")
            },
            hide_index=True,
            use_container_width=True
        )

        # 归纳导出
        st.divider()
        if st.button("✅ 归纳相同折扣并准备下载"):
            # 过滤逻辑：力度 >= 5 且不是无参考价的
            final_df = edited_df[(edited_df['建议力度'] >= 5) & (~edited_df['类型'].str.contains("无参考价"))]
            
            if not final_df.empty:
                # 按折扣力度分组连接 ASIN
                grouped = final_df.groupby('建议力度')['ASIN'].apply(lambda x: ';'.join(list(set(x)))).reset_index()
                
                st.write("### 归纳结果")
                st.dataframe(grouped, use_container_width=True)
                
                # 生成下载
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    # 按照简单提报格式输出
                    out_df = pd.DataFrame({
                        "ASIN列表": grouped['ASIN'],
                        "折扣比例": grouped['建议力度'],
                        "名称": [f"Save {d}%" for d in grouped['建议力度']],
                        "预算": 1000
                    })
                    out_df.to_excel(writer, index=False, sheet_name='提报单')
                
                st.download_button(
                    label="📥 点击下载 Excel 提报文件",
                    data=output.getvalue(),
                    file_name="Amazon_Fixed_Coupons.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("没有可提报的 ASIN。")

if __name__ == "__main__":
    main()
