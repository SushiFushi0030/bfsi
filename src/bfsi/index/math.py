"""User-specified unweighted formulas. Fractions retain exact intermediate ratios."""
from fractions import Fraction
from decimal import Decimal, localcontext, ROUND_HALF_UP

def shown(value, places=1):
    with localcontext() as ctx:
        ctx.prec=40
        f=Fraction(value)
        d=Decimal(f.numerator)/Decimal(f.denominator)
        return float(d.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP))

def direction(bullish, bearish, mixed):
    assert all(isinstance(x,int) and x>=0 for x in [bullish,bearish,mixed])
    n=bullish+bearish+mixed
    score=Fraction(bullish-bearish,n) if n else Fraction(0)
    bfsi=50+50*score
    return {'directional_comments':n,'score':score,'bfsi':bfsi,'no_directional_signal':n==0}

def disagreement(bullish,bearish):
    assert all(isinstance(x,int) and x>=0 for x in [bullish,bearish])
    n=bullish+bearish
    value=1-Fraction(abs(bullish-bearish),n) if n else Fraction(0)
    return {'count':n,'ratio':value,'no_bullish_bearish_signal':n==0}

def rate(numerator, denominator, label):
    assert 0<=numerator<=denominator and denominator>0
    r=Fraction(numerator,denominator)
    return {'count':numerator,'denominator':denominator,'denominator_label':label,
            'ratio':float(r),'percent':float(100*r),'display_percent':shown(100*r),
            'unit':'percent_0_100'}

def formula_checks():
    checks=[]
    for b,s,m,expected,empty in [(0,0,0,50,True),(3,0,0,100,False),(0,3,0,0,False),
                               (2,2,0,50,False),(0,0,4,50,False),(2,1,1,Fraction(125,2),False)]:
        result=direction(b,s,m)
        assert result['bfsi']==expected and result['no_directional_signal']==empty
        checks.append({'bullish':b,'bearish':s,'mixed':m,'bfsi':float(result['bfsi']),
                       'no_directional_signal':empty,'passed':True})
    for b,s,expected in [(0,0,0),(3,0,0),(0,3,0),(2,2,1),(2,1,Fraction(2,3))]:
        assert disagreement(b,s)['ratio']==expected
    return {'bfsi_cases':checks,'disagreement_cases_passed':5}
