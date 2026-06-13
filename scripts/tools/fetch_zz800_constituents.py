"""
爬取新浪中证800最新成分股列表（含科创板），更新 zz800_constituents.csv
"""
import requests
import pandas as pd
import time
import re

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://vip.stock.finance.sina.com.cn/',
}

all_stocks = []

for page in range(1, 21):
    url = f"http://vip.stock.finance.sina.com.cn/corp/go.php/vII_NewestComponent/indexid/000906.phtml?page={page}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = 'gbk'
        text = r.text

        # 解析表格行: <td>代码</td><td>名称</td><td>板块</td><td>日期</td>
        # 新浪页面格式: <a href="...code=XXXXXX">XXXXXX</a>
        rows = re.findall(
            r'<a href="[^"]*code=(\d{6})">(\d{6})</a></td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>',
            text, re.DOTALL
        )
        if not rows:
            # 尝试另一种格式
            rows = re.findall(
                r'<a href="[^"]*code=(\d{6})">',
                text
            )
            if not rows:
                print(f"  第{page}页: 未找到数据，跳过")
                continue
            # 简化处理：只提取代码
            for code in rows:
                all_stocks.append({'code': code, 'name': '', 'board': ''})
        else:
            for row in rows:
                code, _, name, board, _ = row
                name = re.sub(r'<[^>]+>', '', name).strip()
                board = re.sub(r'<[^>]+>', '', board).strip()
                all_stocks.append({'code': code, 'name': name, 'board': board})

        print(f"  第{page}页: 获取 {len(rows)} 条，累计 {len(all_stocks)} 条")
        time.sleep(0.5)

    except Exception as e:
        print(f"  第{page}页出错: {e}")
        continue

# 去重（按代码）
df = pd.DataFrame(all_stocks)
df = df.drop_duplicates(subset='code', keep='first')

print(f"\n总计: {len(df)} 只成分股")

# 统计
kc = df[df['code'].str.startswith('688')]
print(f"科创板(688xxx): {len(kc)} 只")
print(f"沪主板(60xxx): {len(df[df['code'].str.startswith('60')])} 只")
print(f"深主板(00xxx): {len(df[df['code'].str.startswith('00')])} 只")
print(f"创业板(30xxx): {len(df[df['code'].str.startswith('30')])} 只")

# 保存
output_path = '/root/zz800_constituents.csv'
df.to_csv(output_path, index=False)
print(f"\n已保存到 {output_path}")

# 显示科创板列表
if len(kc) > 0:
    print("\n科创板成分股:")
    for _, row in kc.iterrows():
        print(f"  {row['code']} {row['name']} {row['board']}")
