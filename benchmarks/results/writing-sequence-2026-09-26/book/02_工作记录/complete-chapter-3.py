# -*- coding: utf-8 -*-
from pathlib import Path
import json, subprocess
b=Path(__file__).resolve().parent.parent
w=b/'02_工作记录'
t='/Users/cuining/Documents/GitHub/story-skill/skills/story-skill/scripts/story.py'
d=json.loads((w/'prepare-3.raw.json').read_text())['delta']
d['summary']='许棠按约在七点四十开始十分钟纸船试映。音量影响楼上休息，她静音后让孩子现场讲述，邻居参与配音获得共同观看的乐趣。许棠按时收场；周岚提出周三八点帮抬缝纫机，许棠结合十点客户通话确认时间。正式电影仍未选片，周岚仍将搬往东河。'
d['review']['checks']={
'causality':{'note':'前两章找到设备、试出小墙面并拍视频，促成这次能兑现的小试映。后排听不见和楼上怕吵同时有效，许棠停止调大声，让孩子讲述并容纳邻居配音；有一起观看的具体回报，未把噪声问题宣告永久解决。','quote':'许棠把视频倒回去，停在船还正着的地方。小满站在墙边，鼓起腮帮，示范自己怎样吹翻了船。阿冬的妹妹笑着说这是台风，还教他把两只手张开，做出风很大的样子。'},
'continuity':{'note':'试映沿用七点四十和十分钟约定、原纸船与蓝凳子。旧友关系仅前进一步：周岚请求搬家协助仍按原安排离开；老贺不临时回来承担全部工作。许棠先核对自己的工作再承诺，照应既有失约。','quote':'“九点以前可以，我十点得跟客户通话。”'},
'constraints':{'note':'本章1536个visible_nonspace_v1字符，不含章名；第三章是此前行动带来的阶段结果，不是全书终局。正式片名、观看方式和搬家承诺待后续，未做封面、联网或平台发布。','quote':'“还没选呢。”'},
'style':{'note':'洗脚盆误认、纸船台风、母亲忍笑与蓝凳旧字承接人物相处，许棠克制熟人动作而非旁白宣告和好。初稿一处把未坐下者称为拥有凳子已改清；通读对白换人、句号位置、无破折号及段间空行，未见格式阻断项。','quote':'周岚将窗帘袋放到脚边，嘴角抿着。许棠认得她忍笑的样子，险些像以前一样用胳膊碰她一下，手抬到半路，转去扶稳了机器。'}}
d['review']['issues']=[]
cs=[('test-time','fact','次日七点四十起十分钟纸船小试映已完成，许棠静音并让孩子讲述，按约收场。','许棠答应只放最后一遍。孩子们把浪声压得很轻，哗啦哗啦，张姨也跟着拍了两下腿。那只湿得发软的船终于被捞出水面，小满冲大家鞠了一躬，头几乎碰到膝盖。'),('moving-help','hook','周岚请求周三上午协助搬缝纫机，搬家车八点来，许棠答应八点下楼，十点须与客户通话。','“那我八点下楼。”'),('screening-open','fact','正式露天电影尚未选片；张姨提出字幕够大、不要只选儿童内容，后排观看效果仍需处理。','张姨吃完面，把碗往凳子下一放，说真放电影的时候，字得大点，她看不清。这一小段没有字幕，许棠却明白她问的是以后。')]
d['changes']=[{'id':i,'kind':k,'text':txt,'source':'第3章','quote':q,'critical':False,'status':'active','tags':['许棠','周岚','老贺']} for i,k,txt,q in cs]
(w/'delta-3.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
r=subprocess.run(['python3','-B',t,'commit','--book',str(b),'--chapter','3','--draft',str(b/'.story/drafts/chapter-3.md'),'--input',str(w/'delta-3.json')],capture_output=True,text=True)
(w/'commit-3.raw.json').write_text(r.stdout,encoding='utf-8')
print(r.stdout)
r.check_returncode()
a=json.loads(r.stdout)
assert Path(a['path']).read_text()==(b/'.story/drafts/chapter-3.md').read_text()
f=b/'创作约定.md'
s=f.read_text()
s=s[:s.index('断点：')]+'断点：第1至3章已逐章正式提交并导出，revision 8；实际正文与最终草稿一致。本次授权范围已完成。长篇未完本；下一章需另获写作授权，不自动继续。正式电影选片与搬家帮助仍待后续展开。\n'
f.write_text(s,encoding='utf-8')
r=subprocess.run(['python3','-B',t,'status','--book',str(b)],capture_output=True,text=True)
(w/'status-final.raw.json').write_text(r.stdout,encoding='utf-8')
print(r.stdout)
r.check_returncode()
