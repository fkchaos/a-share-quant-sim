#!/usr/bin/env python3
"""行业轮动调研：获取申万行业分类"""
import sys, os, time, json
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/root/a-share-quant-sim")
import numpy as np, pandas as pd
import akshare as ak

DAILY_DIR = "/root/data/daily"
codes = set([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
print(f"股票池: {len(codes)}")

# 申万一级行业代码
SW_INDUSTRIES = [
    ('801010', '农林牧渔'),
    ('801020', '采掘'),
    ('801030', '化工'),
    ('801040', '钢铁'),
    ('801050', '有色金属'),
    ('801080', '电子'),
    ('801110', '家用电器'),
    ('801120', '食品饮料'),
    ('801130', '纺织服装'),
    ('801140', '轻工制造'),
    ('801150', '医药生物'),
    ('801160', '公用事业'),
    ('801170', '交通运输'),
    ('801180', '房地产'),
    ('801200', '商贸'),
    ('801210', '银行'),
    ('801220', '非银金融'),
    ('801230', '综合'),
    ('801710', '建筑材料'),
    ('801720', '建筑装饰'),
    ('801730', '电气设备'),
    ('801740', '机械设备'),
    ('801750', '计算机'),
    ('801760', '传媒'),
    ('801770', '通信'),
    ('801780', '汽车'),
    ('801880', '国防军工'),
    ('801890', '休闲服务'),
]

print("\n[Step 1] 获取申万行业成分股...")
industry_map = {}

for ind_code, ind_name in SW_INDUSTRIES:
    try:
        df = ak.index_stock_cons(symbol=ind_code)
        if '品种代码' in df.columns:
            stock_list = df['品种代码'].tolist()
        elif 'code' in df.columns:
            stock_list = df['code'].tolist()
        else:
            stock_list = df.iloc[:, 0].tolist()
        
        for s in stock_list:
            s = str(s).zfill(6)
            if s in codes:
                industry_map[s] = ind_name
        
        matched = len([s for s in stock_list if str(s).zfill(6) in codes])
        print(f"  {ind_name}: {matched} 只")
        time.sleep(0.3)  # 限速
        
    except Exception as e:
        print(f"  {ind_name}: 失败 - {e}")

print(f"\n总覆盖: {len(industry_map)} / {len(codes)} 只")

# 行业分布
from collections import Counter
ind_counts = Counter(industry_map.values())
print(f"\n行业分布 (有数据的):")
for ind, cnt in ind_counts.most_common():
    print(f"  {ind:>12}: {cnt} 只")

# 保存
with open('/tmp/industry_map.json', 'w') as f:
    json.dump(industry_map, f, ensure_ascii=False, indent=2)
print(f"\n行业映射已保存: /tmp/industry_map.json")
