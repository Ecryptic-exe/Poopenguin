"""Python renders ten B10 figures and fixed-template numerical observations.

Published exclusive grade counts identify categorical quantiles, not exact
score percentiles. No within-grade interpolation, LLM, tags or network calls.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import analyze_chunirec_numeric as core
import compare_chunirec_rating_reference as reference

PERCENTILES = (5,10,30,50,70,90,95)
GRADES = core.LOW_TO_HIGH
RULES = {
    'minimum_bucket_players':100,
    'tail_candidate_minimum_players':200,
    'consecutive_buckets':3,
    'mean_reference_margin_points':1000,
    'wide_P10_P90_grade_steps':3,
    'upper_candidate_P95_minimum_grade':'SSS+',
    'upper_candidate_P50_maximum_grade':'SS+',
    'lower_candidate_P50_minimum_grade':'SSS',
    'lower_candidate_P5_maximum_grade':'SS',
    'trend_minimum_buckets':5,
    'trend_minimum_absolute_spearman':0.6,
    'mean_reference_trend_minimum_range_points':3000,
    'width_trend_minimum_grade_step_range':2,
    'threshold_sensitivity_minimum_rating_range':0.1,
    'mean_benchmark':'score/Rating formula reference, not an empirical across-chart baseline',
    'tail_meaning':'categorical tail candidates, not a diagnosis of player aptitude',
    'grade_width_meaning':'number of source grade boundaries; not score variance or equal score intervals',
    'time_meaning':'one snapshot across ability buckets; no temporal trend claims',
}


def grade_counts(group):
    rank=group['rank']
    return [group['players']-sum(rank.values()),rank['s'],rank['ss'],rank['ssp'],
            rank['sss'],rank['sssp'],rank['max']]


def quantile_index(counts, percent):
    if len(counts)!=len(GRADES) or any(type(v) is not int or v<0 for v in counts):
        raise ValueError('invalid exclusive grade counts')
    if not 1<=percent<=99 or type(percent) is not int:
        raise ValueError('integer percentile outside 1..99')
    n=sum(counts)
    if not n:
        return None
    # Exact nearest-rank ECDF inverse, without floating-point rounding.
    position=(n*percent+99)//100
    running=0
    for i,count in enumerate(counts):
        running+=count
        if running>=position:
            return i
    raise ValueError('unreachable quantile')


def distributions(groups,minimum):
    rows=[]
    for g in groups:
        counts=grade_counts(g)
        if sum(counts)!=g['players']:
            raise ValueError('grade population mismatch')
        q={f'P{p}':quantile_index(counts,p) for p in PERCENTILES}
        if g['players']:
            assert list(q.values())==sorted(q.values())
        entropy=-sum((n/g['players'])*math.log2(n/g['players']) for n in counts if n)
        row={'rating':g['rating'],'label':g['label'],'cls':g['cls'],'players':g['players'],
             'eligible':g['players']>=minimum,'average_score':g['average_score'],
             **{k:None if i is None else GRADES[i] for k,i in q.items()},
             **{k+'_index':i for k,i in q.items()},
             'P10_P90_grade_steps':None if q['P10'] is None else q['P90']-q['P10'],
             'grade_entropy_bits':entropy,'source_locator':g['source_locator']}
        rows.append(row)
    return rows


def ranked(values):
    order=sorted(range(len(values)),key=lambda i:values[i])
    out=[0.0]*len(values)
    i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:
            j+=1
        for k in order[i:j]:
            out[k]=(i+j-1)/2+1
        i=j
    return out


def spearman(xs,ys):
    if len(xs)!=len(ys) or len(xs)<2:
        return None
    x,y=ranked(xs),ranked(ys)
    mx,my=statistics.mean(x),statistics.mean(y)
    numerator=sum((a-mx)*(b-my) for a,b in zip(x,y))
    denominator=math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
    return numerator/denominator if denominator else None


def runs(rows,predicate,min_length=1):
    result=[];current=[]
    for row in rows:
        if predicate(row):
            if current and row['cls']!=current[-1]['cls']+1:
                if len(current)>=min_length:result.append(current)
                current=[]
            current.append(row)
        else:
            if len(current)>=min_length:result.append(current)
            current=[]
    if len(current)>=min_length:result.append(current)
    return result


def range_text(group_runs):
    return '、'.join(f"{r[0]['label']}–{r[-1]['label']}" if len(r)>1 else r[0]['label'] for r in group_runs)


def analyze(manifest,stats,groups,minimum,view_min,view_max):
    constant=manifest['chart']['const']
    dist=distributions(groups,minimum)
    shown=[r for r in dist if view_min<=r['rating']<=view_max]
    usable=[r for r in shown if r['eligible']]
    comparisons=reference.compare_groups(groups,constant,minimum)
    means=[r for r in comparisons if r['eligible'] and view_min<=float(r['best_average_rating'])<=view_max]
    thresholds=reference.grade_thresholds(groups,constant,minimum)
    threshold_rows,curves=core.threshold_table([g for g in groups if view_min<=g['rating']<=view_max],minimum)
    # Use the same shown cohort for the R50 captions and the plotted fits.
    thresholds=reference.grade_thresholds([g for g in groups if view_min<=g['rating']<=view_max],constant,minimum)
    lines=[f"顯示 {len(usable)} 個分布分組、{sum(r['players'] for r in usable):,} 人；每組至少 {minimum} 人。"]
    features=[]
    def add(key,text,evidence):
        features.append({'id':key,'message':text,'evidence':evidence})
        lines.append(text)
    if usable:
        width=[r['P10_P90_grade_steps'] for r in usable]
        add('grade_bandwidth',f"P10–P90 的級別跨度為 {min(width)}–{max(width)} 級（級別並非等分數間距）。",
            {'minimum':min(width),'maximum':max(width)})
        wide=runs(shown,lambda r:r['eligible'] and r['P10_P90_grade_steps']>=RULES['wide_P10_P90_grade_steps'],RULES['consecutive_buckets'])
        if wide:
            add('wide_grade_distribution_candidate',f"分布帶寬較大的連續區間：{range_text(wide)}（個人差候選）。",{'intervals':[[r[0]['label'],r[-1]['label']] for r in wide]})
        upper=[r for r in usable if r['players']>=RULES['tail_candidate_minimum_players'] and r['P95_index']>=5 and r['P50_index']<=3]
        lower=[r for r in usable if r['players']>=RULES['tail_candidate_minimum_players'] and r['P50_index']>=4 and r['P5_index']<=2]
        if upper:
            add('strong_upper_tail_candidate','P95≥SSS+、P50≤SS+ 的分組：'+ '、'.join(r['label'] for r in upper)+'（上尾優勢候選）。',
                {'minimum_players':RULES['tail_candidate_minimum_players'],'groups':[r['label'] for r in upper]})
        if lower:
            add('weak_lower_tail_candidate','P50≥SSS、P5≤SS 的分組：'+ '、'.join(r['label'] for r in lower)+'（下尾落差候選）。',
                {'minimum_players':RULES['tail_candidate_minimum_players'],'groups':[r['label'] for r in lower]})
        rho=spearman([r['rating'] for r in usable],width)
        if len(usable)>=RULES['trend_minimum_buckets'] and max(width)-min(width)>=RULES['width_trend_minimum_grade_step_range'] and rho is not None and abs(rho)>=RULES['trend_minimum_absolute_spearman']:
            direction='縮小' if rho<0 else '擴大'
            add('grade_bandwidth_response',f"P10–P90 級別帶隨能力分組{direction}（Spearman ρ={rho:.2f}；含級別壓縮與封頂影響）。",{'spearman':rho,'grade_step_range':max(width)-min(width)})
    before=[r for r in means if r['reference_status']=='unique_continuous_inverse']
    mean_rows=[{'cls':round(float(r['best_average_rating'])*10),'label':r['best_average_rating'],'gap':r['mean_minus_reference_score']} for r in before]
    margin=RULES['mean_reference_margin_points']
    for name,sign in (('below',-1),('above',1)):
        intervals=runs(mean_rows,lambda r:sign*r['gap']>=margin,RULES['consecutive_buckets'])
        if intervals:
            direction='低於' if sign<0 else '高於'
            add('formula_reference_'+name,f"連續至少三組平均分{direction}公式參考 ≥{margin:,} 分：{range_text(intervals)}。",{'intervals':[[r[0]['label'],r[-1]['label']] for r in intervals]})
    if len(before)>=RULES['trend_minimum_buckets']:
        gaps=[r['mean_minus_reference_score'] for r in before]
        rho=spearman([float(r['best_average_rating']) for r in before],gaps)
        if rho is not None and abs(rho)>=RULES['trend_minimum_absolute_spearman'] and max(gaps)-min(gaps)>=RULES['mean_reference_trend_minimum_range_points']:
            direction='增加' if rho>0 else '減少'
            add('formula_deviation_response',f"平均分與公式的差值隨能力分組{direction}（Spearman ρ={rho:.2f}；差值範圍 {min(gaps):+,.0f}～{max(gaps):+,.0f} 分）。",{'spearman':rho,'minimum_gap':min(gaps),'maximum_gap':max(gaps)})
    by_grade={t['grade']:t for t in thresholds}
    if by_grade['SSS']['observed_R50'] is not None and by_grade['SSS+']['observed_R50'] is not None:
        a,b=by_grade['SSS']['observed_R50'],by_grade['SSS+']['observed_R50']
        add('SSS_to_SSSplus_R50',f"SSS／SSS+ 的 50% 達成門檻：{a:.2f}／{b:.2f}；間距 {b-a:.2f}（公式間距 0.15）。",{'SSS_R50':a,'SSSplus_R50':b,'gap':b-a})
    for target in ('SS+','SSS','SSS+'):
        chosen=[r for r in threshold_rows if r['grade']==target]
        by_p={r['achievement_probability']:r['estimate'] for r in chosen}
        if by_p.get(.1) is not None and by_p.get(.9) is not None:
            add('achievement_response_width_'+target,f"{target} 達成率由 10% 到 90% 的能力區間：{by_p[.1]:.2f}–{by_p[.9]:.2f}（跨度 {by_p[.9]-by_p[.1]:.2f}）。",{'R10':by_p[.1],'R90':by_p[.9]})
    representative=reference.representative_rows(means,constant)
    for row in representative:
        lines.append(f"{row['best_average_rating']} 組平均 {row['actual_mean_score']:,.0f} 分，與公式參考差 {row['mean_minus_reference_score']:+,.0f} 分。")
    sensitivity=[]
    for min_n in (50,100,200,500):
        table=reference.grade_thresholds([g for g in groups if view_min<=g['rating']<=view_max],constant,min_n)
        sensitivity.extend({'min_players':min_n,**t} for t in table)
    for grade in ('SS+','SSS','SSS+'):
        estimates=[r['observed_R50'] for r in sensitivity if r['grade']==grade and r['observed_R50'] is not None]
        if len(estimates)>1 and max(estimates)-min(estimates)>RULES['threshold_sensitivity_minimum_rating_range']+1e-10:
            add('sample_filter_sensitivity_'+grade,f"{grade} 的 R50 對最低組人數設定較敏感：估計範圍 {min(estimates):.2f}–{max(estimates):.2f}。",{'estimate_range':[min(estimates),max(estimates)]})
    lines.append('分位由原始級別人數計算；OTHER 保留來源名稱，S 未另細分 S+；不推估級別內的精確分數。')
    return {'distributions':dist,'comparison':comparisons,'thresholds':thresholds,'threshold_table':threshold_rows,
        'curves':curves,'sensitivity':sensitivity,'features':features,'conclusions':lines,
        'total_players':stats['players'],'published_bucket_players':sum(g['players'] for g in groups),
        'shown_eligible_players':sum(r['players'] for r in usable),'shown_eligible_buckets':len(usable)}


def render(path,job,manifest,data,minimum,view_min,view_max):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.ticker import FuncFormatter,MultipleLocator
    plt.rcParams.update({'font.family':['Microsoft JhengHei','Yu Gothic','Microsoft YaHei','DejaVu Sans'],
                         'axes.unicode_minus':False,'font.size':10})
    fig,axs=plt.subplots(3,1,figsize=(13,12.2),sharex=True,
        gridspec_kw={'height_ratios':[1.35,1.4,1]},layout='constrained')
    ax,band,rate=axs
    fig.suptitle(f"B10 #{job['rank']:02d} · {job['title']}／{job['difficulty']}",fontsize=19,fontweight='bold')
    constant=manifest['chart']['const']
    confirmation='已確認' if manifest['chart']['const_confirmed'] else '來源尚未確認'
    ax.set_title(f"平均分與公式參考｜Chunirec 定數 {constant}（{confirmation}）｜每組 ≥{minimum} 人",fontsize=11)
    raw=data['comparison']
    by_label={r['best_average_rating']:r for r in raw}
    # Include all source axis labels, so insufficient/missing groups create gaps.
    raw_x=[view_min+i/10 for i in range(round((view_max-view_min)*10)+1)]
    xs=np.array(raw_x)
    labels=[f'{x:.2f}' for x in xs]
    ys=np.array([by_label[label]['actual_mean_score'] if label in by_label and by_label[label]['eligible'] else np.nan for label in labels])
    ax.plot(xs,ys,color='#246CAB',marker='o',ms=4,lw=2.1,label='分組平均分')
    cap=float(reference.dec(constant)+reference.dec('2.15'))
    end=min(view_max,cap)
    if view_min<end:
        cx=np.linspace(view_min,end,300)
        cy=[reference.inverse_rating(f'{x:.10f}',constant)['score'] for x in cx]
        if math.isclose(end,cap):cy[-1]=1009000
        ax.plot(cx,cy,color='#C87D25',lw=2,label='公式反推分數')
    if cap<=view_max:
        ax.plot([max(cap,view_min),view_max+.035],[1009000,1009000],color='#C87D25',ls='--',lw=1.6,label='封頂後 SSS+ 門檻')
        for a in axs:a.axvspan(max(cap,view_min),view_max+.04,color='#EEEEEE',zorder=0)
    for score,grade in ((1000000,'SS'),(1005000,'SS+'),(1007500,'SSS'),(1009000,'SSS+')):
        ax.axhline(score,color='#AAAAAA',ls=':',lw=.7,zorder=0)
        ax.text(view_max+.025,score,grade,ha='left',va='center',fontsize=9,color='#666666')
    finite=ys[np.isfinite(ys)]
    ax.set_ylim(max(0,min(finite)-1800) if len(finite) else 975000,1011000)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v:,.0f}'))
    ax.set_ylabel('分數')
    ax.legend(loc='lower right',fontsize=9)
    dist={r['label']:r for r in data['distributions']}
    quantiles={p:np.array([dist[label][f'P{p}_index'] if label in dist and dist[label]['eligible'] else np.nan for label in labels],dtype=float) for p in PERCENTILES}
    for lo,hi,color,name in ((5,95,'#D6E8F3','P5–P95'),(10,90,'#A6CCE2','P10–P90'),(30,70,'#609EBE','P30–P70')):
        band.fill_between(xs,quantiles[lo],quantiles[hi],step='mid',color=color,label=name)
        for p in (lo,hi):band.step(xs,quantiles[p],where='mid',color='#447A9F',lw=.65,alpha=.7)
    band.step(xs,quantiles[50],where='mid',color='#142F54',lw=2.5,marker='o',ms=3.5,label='P50 中位數')
    band.set_title('成績級別分位帶｜七個分位均由級別人數直接計算',fontsize=11)
    band.set_yticks(range(len(GRADES)),GRADES)
    band.set_ylabel('來源級別（非等分數間距）')
    band.set_ylim(-.75,6.35)
    for x,label in zip(xs,labels):
        r=dist.get(label)
        if r and r['eligible']:band.text(x,-.5,f"n={r['players']}",ha='center',va='center',fontsize=6.9,color='#526273')
    band.legend(loc='upper left',ncol=4,fontsize=9)
    g=[r for r in data['distributions'] if r['eligible'] and view_min<=r['rating']<=view_max]
    source_groups={x['label']:x for x in manifest['_groups']}
    colors={'ssp':'#CB8522','sss':'#41876D','sssp':'#7A62A7'}
    for target in ('ssp','sss','sssp'):
        tx=[r['rating'] for r in g]
        raw_rate=[core.successes(source_groups[r['label']],target)/r['players']*100 for r in g]
        fit=[data['curves'][target][r['cls']]*100 for r in g]
        rate.plot(tx,fit,color=colors[target],lw=2,label=core.GRADE_LABEL[target])
        rate.scatter(tx,raw_rate,color=colors[target],s=17,alpha=.8)
    rate.axhline(50,color='#666666',ls=':',lw=.8)
    rate.set_ylim(-3,105)
    rate.set_yticks([0,25,50,75,100],['0%','25%','50%','75%','100%'])
    rate.set_title('成績線達成率｜點：原始比率；線：依人數加權的單調擬合',fontsize=11)
    rate.set_ylabel('達成率')
    rate.legend(loc='upper left',ncol=3,fontsize=9)
    rate.set_xlabel('BEST 枠平均分組標籤（不是玩家總 Rating）',fontsize=11)
    rate.xaxis.set_major_locator(MultipleLocator(.1))
    rate.xaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v:.2f}'))
    rate.set_xlim(view_min-.045,view_max+.09)
    for a in axs:
        a.spines[['top','right']].set_visible(False)
        a.grid(axis='y',alpha=.18)
    thresholds={t['grade']:t['observed_R50'] for t in data['thresholds']}
    positions=' · '.join(grade+': '+(f'{v:.2f}' if v is not None else '範圍內無法估計') for grade,v in thresholds.items() if grade!='SS')
    fig.supxlabel('50% 達成門檻  '+positions+'\n公式僅供對照；級別內分數未知；線性插值門檻不是信賴區間。',fontsize=9,color='#4D5C6C')
    fig.savefig(path,dpi=145)
    plt.close(fig)


def render_missing(path,job,error):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family']=['Microsoft JhengHei','Microsoft YaHei','DejaVu Sans']
    fig=plt.figure(figsize=(13,12.2))
    fig.text(.08,.87,f"B10 #{job['rank']:02d} · {job['title']}／{job['difficulty']}",fontsize=20)
    fig.text(.08,.68,'來源資料不足，保留為缺失',fontsize=24,color='#AD5B46')
    fig.text(.08,.52,'本張未計算實測曲線或分位。\n失敗原因與 URL 已另存。\n沒有補零或推估。',fontsize=16,linespacing=1.9)
    fig.savefig(path,dpi=145)
    plt.close(fig)


def run(args):
    batch_path=args.batch.resolve()
    batch=json.loads(batch_path.read_text(encoding='utf-8'))
    if len(batch['jobs'])!=10 or {j['rank'] for j in batch['jobs']}!=set(range(1,11)):
        raise ValueError('requires exactly ten distinct B10 jobs')
    if core.sha256(Path(batch['player_csv']))!=batch['player_csv_sha256']:
        raise ValueError('B10 player input changed')
    wiki=args.wiki_file.resolve()
    formula=reference.extract_formula(wiki.read_bytes())
    output=args.output.resolve() if args.output else batch_path.parent/('quantile_preview_'+datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d_%H%M%S'))
    output.mkdir(parents=True,exist_ok=False)
    (output/'wiki_rating_source.html').write_bytes(wiki.read_bytes())
    core.save_json(output/'rules.json',RULES)
    outcomes=[];summary=[];report=[]
    for job in batch['jobs']:
        stem=f"{job['rank']:02d}_"+re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',job['title']).strip(' .')
        path=output/(stem+'.png')
        try:
            snapshot=Path(job.get('snapshot') or Path(job['job_folder'])/'source_snapshot')
            manifest,stats,groups,hashes=core.load_input(snapshot)
            if (manifest['title'],manifest['difficulty'])!=(job['title'],job['difficulty']):
                raise ValueError('chart identity mismatch')
            data=analyze(manifest,stats,groups,args.min_players,args.view_min,args.view_max)
            if not data['shown_eligible_buckets']:
                raise ValueError('no sufficiently populated displayed buckets')
            render(path,job,{**manifest,'_groups':groups},data,args.min_players,args.view_min,args.view_max)
            core.write_csv(output/(stem+'_quantiles.csv'),data['distributions'])
            core.write_csv(output/(stem+'_thresholds.csv'),data['threshold_table'])
            core.write_csv(output/(stem+'_sample_sensitivity.csv'),data['sensitivity'])
            data.pop('curves')
            record={'rank':job['rank'],'title':job['title'],'difficulty':job['difficulty'],'status':'complete',
                'image':path.name,'source_url':manifest['source_url'],'input_snapshot':str(snapshot),
                'source_sha256':hashes,'source_constant':manifest['chart']['const'],
                'source_constant_confirmed':manifest['chart']['const_confirmed'],
                'record_constant':job['record_constant'],'statistics_region':None,'statistics_version':None,
                'player_csv_region':'INT','axis_label':'BEST 枠平均','llm_used':False,'tags_generated':False,
                'collector_initial_status':job['status'],'offline_validation_passed':True,**data}
            if not all(core.sha256(snapshot/name)==h for name,h in hashes.items()):
                raise ValueError('original snapshot changed')
            core.save_json(output/(stem+'.json'),record)
            thresholds={t['grade']:t['observed_R50'] for t in data['thresholds']}
            summary.append({'rank':job['rank'],'title':job['title'],'difficulty':job['difficulty'],'status':'complete',
                'source_constant':manifest['chart']['const'],'record_constant':job['record_constant'],
                'eligible_buckets':data['shown_eligible_buckets'],'eligible_players':data['shown_eligible_players'],
                'SSplus_R50':thresholds['SS+'],'SSS_R50':thresholds['SSS'],'SSSplus_R50':thresholds['SSS+'],'image':path.name})
            report.append(f"### {job['rank']:02d} · {job['title']}／{job['difficulty']}\n\n"+'\n'.join('- '+s for s in data['conclusions'])
                          +'\n\n來源：'+manifest['source_url']+'\n')
            status='complete'
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
            render_missing(path,job,error)
            core.save_json(output/(stem+'.json'),{'rank':job['rank'],'title':job['title'],'difficulty':job['difficulty'],
                'status':'insufficient_data','reason':error,'image':path.name,'source_url':job.get('source_url'),
                'llm_used':False,'automatic_retry':False})
            summary.append({'rank':job['rank'],'title':job['title'],'difficulty':job['difficulty'],'status':'insufficient_data',
                'source_constant':None,'record_constant':job['record_constant'],'eligible_buckets':0,'eligible_players':0,
                'SSplus_R50':None,'SSS_R50':None,'SSSplus_R50':None,'image':path.name})
            report.append(f"### {job['rank']:02d} · {job['title']}／{job['difficulty']}\n\n資料不足：{error}\n")
            status='insufficient_data'
        outcomes.append({'rank':job['rank'],'title':job['title'],'status':status,'image':path.name})
        print(json.dumps(outcomes[-1],ensure_ascii=False),flush=True)
    core.write_csv(output/'numeric_summary.csv',summary)
    (output/'CONCLUSION.md').write_text('# B10 純 Python 數值預覽\n\n'
        +'所有圖片、數值與文字由 Python 固定規則輸出。橫軸為 BEST 枠平均，分位是来源級別分位，公式並非跨譜面實測基準。\n\n'
        +'\n'.join(report),encoding='utf-8')
    images=list(output.glob('*.png'))
    if len(images)!=10:raise ValueError('expected ten PNG images')
    from PIL import Image
    for image in images:
        with Image.open(image) as im:
            if im.width<1000 or im.height<1000:raise ValueError('unexpected image size')
            im.verify()
    archive=output/'B10_python_figures.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for image in images:z.write(image,image.name)
        z.write(output/'CONCLUSION.md','CONCLUSION.md')
        z.write(output/'numeric_summary.csv','numeric_summary.csv')
    core.save_json(output/'validation.json',{'status':'passed','expected_images':10,'actual_images':10,
        'valid_data_figures':sum(r['status']=='complete' for r in outcomes),'llm_used':False,'tags_generated':False,
        'batch_sha256':core.sha256(batch_path),'player_input_unchanged':core.sha256(Path(batch['player_csv']))==batch['player_csv_sha256'],
        'analyzer_sha256':core.sha256(Path(__file__)),'core_sha256':core.sha256(Path(core.__file__)),
        'reference_sha256':core.sha256(Path(reference.__file__)),'formula_source':formula,
        'quantile_definition':'smallest source grade with cumulative count >= ceil(N*p); integer arithmetic',
        'view_range':[args.view_min,args.view_max],'min_players':args.min_players,'outcomes':outcomes,
        'output_sha256':{p.name:core.sha256(p) for p in output.iterdir() if p.is_file()}})
    print(json.dumps({'output':str(output),'images':len(images),'data_figures':sum(r['status']=='complete' for r in outcomes)},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',required=True,type=Path)
    p.add_argument('--wiki-file',type=Path,default=core.ROOT/'table_probes/chunirec_Air_ULTIMA/20261004_100328/rating_reference_20261004_105530/wiki_rating_source.html')
    p.add_argument('--output',type=Path)
    p.add_argument('--min-players',type=int,default=100)
    p.add_argument('--view-min',type=float,default=16.0)
    p.add_argument('--view-max',type=float,default=17.7)
    args=p.parse_args()
    if args.min_players<1 or args.view_min>=args.view_max:raise ValueError('invalid parameters')
    run(args)
