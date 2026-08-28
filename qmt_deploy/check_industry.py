#coding:gbk
"""
Check what format IndustryClassification returns.
Load this in QMT, check the print output.
"""
def init(C):
    test_codes = ['000001.SZ', '600000.SH', '000030.SZ', '002384.SZ', '601138.SH']
    print('='*50)
    print('[CHECK] IndustryClassification format test')
    print('='*50)
    for code in test_codes:
        try:
            detail = C.get_instrument_detail(code)
            industry = detail.get('IndustryClassification', 'NOT_FOUND')
            print('[%s] IndustryClassification = %r (type=%s)' % (code, industry, type(industry).__name__))
        except Exception as e:
            print('[%s] ERROR: %s' % (code, e))
    print('='*50)

def handlebar(C):
    pass
