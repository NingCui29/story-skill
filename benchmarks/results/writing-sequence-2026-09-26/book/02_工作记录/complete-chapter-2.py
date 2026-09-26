# -*- coding: utf-8 -*-
from pathlib import Path
import json, subprocess
b=Path(__file__).resolve().parent.parent
w=b/'02_工作记录'
t='/Users/cuining/Documents/GitHub/story-skill/skills/story-skill/scripts/story.py'
d=json.loads((w/'prepare-2.raw.json').read_text())['delta']
d['summary']='次日许棠租来便携投影；张姨晾床单、面馆灯箱和搬家通道迫使她缩小画面改到自家门洞外。老贺按约离开照护妻子。许棠承认三年前主动留下应酬并延迟告知，周岚未原谅但允许孩子次日七点四十看十分钟纸船测试片。'
d['review']['checks']={
'causality':{'note':'许棠想用整面白墙，晾晒和营业灯光构成有效回应。她没有要求邻居停下生活，而是试不同位置并缩小画面；靠主动调整得到可试映的局部结果。承认聚餐出于害怕被排除，是责任说明，不是免罪苦衷。','quote':'最后，自家门洞外那截背着灯箱的墙，接住了一小块清楚的蓝色。'},
'continuity':{'note':'承接第1章找机器和旧桌堵路，老贺于次日六点半前提供有限帮助。周岚仍要搬家，无已原谅判断。临场失约保持招工实操和等照看孩子的原事件，新增聚餐细节与先前相容。','quote':'“我知道，那天晚上你发了聚餐照片。”'},
'constraints':{'note':'本章1499个visible_nonspace_v1字符，不含章名；没有反派或强反转。周岚为孩子作息给试映设置条件，老贺有照护妻子的具体责任；电影仍仅处于测试准备。','quote':'“只能看十分钟，回来洗澡别赖。”'},
'style':{'note':'晒床单、样衣、帮扶门和拍纸船各自改变当下选择。周岚的谢谢仅对应扶门，限知叙述没有宣告和好。通读核对语序、对白归属、单对引号句号及无破折号；留有长短段落与生活停顿。','quote':'她知道这两个字只谢这一件事，却还是舍不得立即把门关上。'}}
d['review']['issues']=[]
cs=[('repair-boundary','fact','老贺完成便携投影检查与基本操作教学后按约离开，需照顾膝痛的妻子。','“你贺婶这几天膝盖疼，洗了澡，我得给她贴膏药。”老贺把工具装回去，“机器会开就够了，别急着放那么大，先看清。”'),('test-time','fact','拟在次日七点四十试映十分钟，周岚同意小满看完回家洗澡，通道须空出。','“明天试十分钟，可以吗？七点四十开始，不占路。”'),('test-material','fact','许棠拍摄小满在塑料盆里吹纸船的小视频，作为次日机器测试片。','小满俯下身吹气，气太大，船翻了。他呆了一下，赶紧伸手捞，满脸严肃地挤船底的水。许棠也笑出了声，把刚拍的那一小段给他看。'),('old-breach','character','许棠承认三年前为了融入公司聚餐未上车又拖延通知；周岚早已从聚餐照片知情，道歉后尚未和好。','“我怕先走了，往后他们不叫我。”她低下头，“又想着饭吃完再走，也许还来得及，就一直没跟你说。”')]
d['changes']=[{'id':i,'kind':k,'text':txt,'source':'第2章','quote':q,'critical':False,'status':'active','tags':['许棠','周岚','老贺']} for i,k,txt,q in cs]
(w/'delta-2.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
r=subprocess.run(['python3','-B',t,'commit','--book',str(b),'--chapter','2','--draft',str(b/'.story/drafts/chapter-2.md'),'--input',str(w/'delta-2.json')],capture_output=True,text=True)
(w/'commit-2.raw.json').write_text(r.stdout,encoding='utf-8')
print(r.stdout)
r.check_returncode()
a=json.loads(r.stdout)
assert Path(a['path']).read_text()==(b/'.story/drafts/chapter-2.md').read_text()
f=b/'创作约定.md'
s=f.read_text()
s=s[:s.index('断点：')]+'断点：第1、2章已正式提交并导出，revision 7；实际正文与草稿一致。前三章工具计划已采用。下一动作：核对第3章细纲与context，起草第3章。\n'
f.write_text(s,encoding='utf-8')
print((b/'01_大纲细纲/第一卷 银幕还没亮/第3章 后排留个位置.md').read_text())
r=subprocess.run(['python3','-B',t,'context','--book',str(b),'--chapter','3','--budget-bytes','16000'],capture_output=True,text=True)
(w/'context-3.raw.json').write_text(r.stdout,encoding='utf-8')
print(r.stdout)
r.check_returncode()
