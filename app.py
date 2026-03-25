import streamlit as st
import pandas as pd
import re
import math

# 设置页面配置
st.set_page_config(page_title="Amazon Coupon 优化助手", layout="wide")

def parse_amazon_errors(error_text):
    """解析亚马逊报错批注：提取 ASIN 和 要求净价"""
    results = []
    # 按 ASIN 模式分割块
    blocks = re.split(r'([A-Z0-9]{10})\n', error_text)
    
    for i in range(1, len(blocks), 2):
        asin = blocks[i].strip()
        content = blocks[i+1]
        
        # 逻辑 1：无参考价（直接标记剔除）
        if "没有经验证的参考价" in content or "没有经验证的历史售价" in content:
            results.append({"ASIN": asin, "类型": "❌ 无参考价 (剔除)", "要求净价": None})
        
        # 逻辑 2：力度不足（提取要求的净价格）
        elif "要求的净价格" in content:
            # 使用正则匹配金额，例如 €40.84 或 $40.84
            price_match = re.search(r'要求的净价格：[^\d]*([\d\.]+)', content)
            req_price = float(price_match.group(1)) if price_match else None
            results.append({"ASIN": asin, "类型": "⚠️ 力度不足 (需增加)", "要求净价": req_price})
            
    return pd.DataFrame(results)

def main():
    st.title("🎯 Amazon Coupon 第二阶段：智能修复与提报")
    st.info("说明：本模块自动计算需增加的力度，并支持按相同折扣合并 ASIN。")

    # --- 1. 数据上传区 ---
    col_up1, col_up2 = st.columns(2)
    with col_up1:
        all_listing_file = st.file_uploader("1. 上传 All Listing 报表 (需包含 asin, price 列)", type=['xlsx', 'csv'])
    with col_up2:
        error_logs = st.text_area("2. 粘贴亚马逊 N 列批注报错内容", height=150, placeholder="B0XXXXXX\n提高优惠券折扣...\n要求的净价格：€37.99...")

    if all_listing_file and error_logs:
        # 读取 All Listing
        df_listing = pd.read_excel(all_listing_file)
        # 统一列名（容错处理）
        df_listing.columns = [c.lower() for c in df_listing.columns]
        
        # 解析报错内容
        df_errors = parse_amazon_errors(error_logs)
        
        # 合并数据获取原价
        df_merge = pd.merge(df_errors, df_listing[['asin', 'price']], left_on='ASIN', right_on='asin', how='left')
        
        # --- 2. 核心计算逻辑 ---
        def calculate_logic(row):
            if "无参考价" in row['类型']:
                return "剔除", 0, "无法提报"
            
            if pd.notnull(row['price']) and pd.notnull(row['要求净价']):
                # 计算折扣百分比 = (原价 - 要求净价) / 原价
                raw_discount = (row['price'] - row['要求净价']) / row['price']
                # 向上取整，例如 14.2% 取 15%，确保比亚马逊要求的高一点点
                final_pct = math.ceil(raw_discount * 100)
                
                # 判断力度是否过大 (例如超过 50% 预警)
                warning = "⚠️ 力度较大" if final_pct > 50 else "正常"
                return f"建议 {final_pct}%", final_pct, warning
            
            return "数据不足", 0, "检查Listing原价"

        df_merge['系统建议'], df_merge['建议力度'], df_merge['风险提示'] = zip(*df_merge.apply(calculate_logic, axis=1))

        # --- 3. 筛选与展示 ---
        st.divider()
        st.subheader("📊 报错详情与调价决策")
        
        # 筛选器
        filter_opt = st.multiselect("只看特定类型：", options=df_merge['类型'].unique(), default=df_merge['类型'].unique())
        display_df = df_merge[df_merge['类型'].isin(filter_opt)]

        # 使用 data_editor 让用户可以手动微调或勾选
        edited_df = st.data_editor(
            display_df[['ASIN', 'price', '要求净价', '类型', '系统建议', '建议力度', '风险提示']],
            column_config={
                "price": "原价",
                "建议力度": st.column_config.NumberColumn("拟提报折扣%", format="%d%%"),
                "保留": st.column_config.CheckboxColumn("是否保留提报?", default=False)
            },
            hide_index=True,
            use_container_width=True
        )

        # --- 4. 归纳与导出 ---
        st.divider()
        if st.button("✅ 按照相同折扣归纳 ASIN 并准备提报"):
            # 过滤掉不保留的（无参考价默认不保留，除非用户勾选）
            final_to_submit = edited_df[edited_df['建议力度'] > 0]
            
            if not final_to_submit.empty:
                # 按建议力度分组，将 ASIN 用分号连接
                grouped = final_to_submit.groupby('建议力度')['ASIN'].apply(lambda x: ';'.join(x)).reset_index()
                
                st.write("### 归纳结果 (同一行代表同一个 Coupon)")
                st.table(grouped)
                
                # 这里可以进一步生成亚马逊提报模板 Excel
                st.success("处理完成！你可以根据上述归纳结果填入第一阶段的模板中。")
            else:
                st.warning("没有选择任何可提报的 ASIN。")

if __name__ == "__main__":
    main()