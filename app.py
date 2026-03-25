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
        
        # 逻辑 A：无参考价（致命错误，需剔除）
        if "没有经验证的参考价" in content or "没有经验证的历史售价" in content:
            results.append({
                "ASIN": asin, 
                "类型": "❌ 无参考价 (剔除)", 
                "要求净价": None,
                "原始报错": "ASIN 没有经验证的历史售价或建议零售价"
            })
        
        # 逻辑 B：力度不足（可修复，需计算）
        elif "要求的净价格" in content:
            # 提取金额数字，例如 €40.84 或 $37.99
            price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(price_match.group(1)) if price_match else None
            results.append({
                "ASIN": asin, 
                "类型": "⚠️ 力度不足 (需增加)", 
                "要求净价": req_price,
                "原始报错": "提高优惠券折扣以符合要求"
            })
    return pd.DataFrame(results)

# --- 函数 2：多格式 Listing 文件读取 ---
def load_listing_data(uploaded_file):
    """适配多种格式，处理亚马逊 TXT 的特殊编码"""
    fname = uploaded_file.name.lower()
    try:
        if fname.endswith('.txt'):
            # 亚马逊 TXT 报表通常是 Tab 分隔且使用 utf-16 编码
            return pd.read_csv(uploaded_file, sep='\t', encoding='utf-16')
        elif fname.endswith('.csv'):
            return pd.read_csv(uploaded_file)
        elif fname.endswith('.xls') or fname.endswith('.xlsx'):
            return pd.read_excel(uploaded_file)
    except Exception as e:
        # 如果 utf-16 失败，尝试常规 utf-8
        try:
            return pd.read_csv(uploaded_file, sep='\t', encoding='utf-8')
        except:
            st.error(f"文件读取失败，请检查格式：{e}")
            return None

# --- 函数 3：核心计算与决策逻辑 ---
def apply_calculation(row):
    """基于原价和要求净价，计算建议折扣百分比"""
    if "无参考价" in str(row['类型']):
        return "剔除", 0, "无法提报"
    
    if pd.notnull(row['price']) and pd.notnull(row['要求净价']):
        # 公式：(原价 - 要求净价) / 原价
        raw_discount_ratio = (row['price'] - row['要求净价']) / row['price']
        # 向上取整，确保 14.1% 变成 15%
        final_pct = math.ceil(raw_discount_ratio * 100)
        
        if final_pct > 80:
            return f"建议 {final_pct}%", final_pct, "‼️ 折扣过大"
        elif final_pct <= 0:
            return "价格异常", 0, "要求净价高于原价"
        else:
            return f"建议 {final_pct}%", final_pct, "正常"
            
    return "缺少价格", 0, "请核对 Listing 表"

# --- 函数 4：导出 Excel 字节流 ---
def get_excel_download(grouped_df):
    """生成符合亚马逊上传格式的二进制数据"""
    output = io.BytesIO()
    # 根据第一阶段要求构造导出列
    export_data = []
    for _, row in grouped_df.iterrows():
        export_data.append({
            "ASIN列表": row['ASIN'],
            "折扣比例": row['建议力度'],
            "优惠券名称": f"Save {row['建议力度']}%",
            "预算": 1000,
            "开始日期": "2026-04-01",
            "结束日期": "2026-04-30"
        })
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(export_data).to_excel(writer, index=False, sheet_name='Coupon提报')
    return output.getvalue()

# --- 主程序入口 ---
def main():
    st.title("🚀 Amazon Coupon 第二阶段：修复与智能归纳")
    st.markdown("---")

    # 1. 文件上传
    col_l, col_r = st.columns(2)
    with col_l:
        listing_file = st.file_uploader("1. 上传 All Listing 报表 (支持 TXT/XLS/XLSX)", type=['txt', 'xls', 'xlsx', 'csv'])
    with col_r:
        error_input = st.text_area("2. 粘贴 N 列批注报错内容", height=150, placeholder="B0xxxx\n没有经验证的参考价...")

    if listing_file and error_input:
        df_listing = load_listing_data(listing_file)
        if df_listing is not None:
            # 统一列名处理
            df_listing.columns = [str(c).lower().strip() for c in df_listing.columns]
            
            # 解析报错
            df_errors = parse_amazon_errors(error_input)
            
            # 关联原价 (假设 Listing 表中有 'asin' 和 'price' 列)
            # 兼容处理：有些表可能叫 'price-amount' 或 'your-price'
            price_col = next((c for c in df_listing.columns if 'price' in c), None)
            asin_col = next((c for c in df_listing.columns if 'asin' in c), None)
            
            if not price_col or not asin_col:
                st.error("All Listing 表中未找到 ASIN 或 Price 列，请检查文件内容。")
                return

            df_merge = pd.merge(df_errors, df_listing[[asin_col, price_col]], left_on='ASIN', right_on=asin_col, how='left')
            df_merge = df_merge.rename(columns={price_col: 'price'})

            # 执行计算
            df_merge['系统决策'], df_merge['建议力度'], df_merge['提示'] = zip(*df_merge.apply(apply_calculation, axis=1))

            # 2. 筛选与决策显示
            st.subheader("📊 调价决策工作台")
            filter_status = st.multiselect("快速筛选类型：", options=df_merge['类型'].unique(), default=df_merge['类型'].unique())
            
            display_df = df_merge[df_merge['类型'].isin(filter_status)]
            
            # 用户编辑区
            edited_df = st.data_editor(
                display_df[['ASIN', 'price', '要求净价', '类型', '系统决策', '建议力度', '提示']],
                column_config={
                    "price": st.column_config.NumberColumn("原价", format="%.2f"),
                    "要求净价": st.column_config.NumberColumn("亚马逊要求价", format="%.2f"),
                    "建议力度": st.column_config.NumberColumn("拟提报折扣%", format="%d%%"),
                    "保留": st.column_config.CheckboxColumn("加入提报?", default=False)
                },
                disabled=["ASIN", "price", "要求净价", "类型", "系统决策", "提示"],
                hide_index=True,
                key="editor"
            )

            # 3. 归纳与下载
            st.divider()
            col_act1, col_act2 = st.columns([1, 2])
            
            with col_act1:
                if st.button("✅ 确认并生成归纳清单"):
                    # 逻辑：只处理用户在编辑表格里确认过的行
                    # 注意：st.data_editor 返回的是完整数据，我们可以直接通过建议力度 > 0 过滤
                    final_to_submit = edited_df[edited_df['建议力度'] > 0]
                    
                    if not final_to_submit.empty:
                        # 按折扣力度分组
                        grouped = final_to_submit.groupby('建议力度')['ASIN'].apply(lambda x: ';'.join(x)).reset_index()
                        st.session_state['result_data'] = grouped
                        st.success("归纳成功！")
                    else:
                        st.warning("无可提报的 ASIN（请检查力度是否大于0）。")

            with col_act2:
                if 'result_data' in st.session_state:
                    st.write("### 最终归纳结果")
                    st.dataframe(st.session_state['result_data'], use_container_width=True)
                    
                    excel_bytes = get_excel_download(st.session_state['result_data'])
                    st.download_button(
                        label="📥 下载提报模板 (XLSX)",
                        data=excel_bytes,
                        file_name="Amazon_Coupon_Final.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

if __name__ == "__main__":
    main()
