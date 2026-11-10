"""One document compiler for interactive preview and granted frame export."""
import json
from pathlib import Path

from app.creation.visual_template_contracts import VisualTemplate

CLOCK_PATH = Path(__file__).parents[1] / 'template_assets' / 'openfilm-clock.json'
POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; "
    "worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")


def template_document(definition: VisualTemplate):
    if definition.dependencies:
        raise ValueError('模板依赖尚不可用：' + '、'.join(definition.dependencies))
    clock = json.loads(CLOCK_PATH.read_text())['clock']
    before = f'<meta http-equiv="Content-Security-Policy" content="{POLICY}"><script>window.filmHost={{}};{clock}'
    before += "let templateFailure=null;addEventListener('error',e=>{templateFailure=e.message});addEventListener('unhandledrejection',e=>{templateFailure=String(e.reason)});</script>"
    before += "<script>addEventListener('securitypolicyviolation',e=>{templateFailure='Template dependency blocked: '+e.violatedDirective});</script>"
    after = '''<script>
  const templateBaseFonts=new WeakMap();
  const templateMeasure=document.createElement('canvas').getContext('2d');
  function fitTemplateText(target,value){
    const style=getComputedStyle(target);
    const base=templateBaseFonts.get(target)??{size:parseFloat(style.fontSize),spacing:parseFloat(style.letterSpacing)||0};
    templateBaseFonts.set(target,base);
    templateMeasure.font=style.fontStyle+' '+style.fontWeight+' '+base.size+'px '+style.fontFamily;
    const width=Math.max(...value.split('\\n').map(line=>templateMeasure.measureText(line).width+Math.max(0,[...line].length-1)*base.spacing),1);
    const size=Math.min(base.size,base.size*innerWidth*.86/width);
    if(size<14)throw Error('模板文字放不下（'+target.className+'），请缩短文字、分行或修改源码布局');
    target.style.fontSize=size+'px';
  }
  addEventListener('message',async e=>{
    if(e.source!==parent||e.data?.kind!=='draw')return;
    const {t,bindings,values,ticket}=e.data;
    try {
      if(templateFailure)throw Error(templateFailure);
      await document.fonts.ready;
      for(const binding of bindings){
        const value=values[binding.key]??binding.default;
        if(binding.kind==='text'){
          const target=document.querySelector(binding.target);
          if(!target)throw Error('Missing template binding: '+binding.target);
          if(target.textContent!==value)target.textContent=value;
          fitTemplateText(target,value);
        } else if(value)document.documentElement.style.setProperty(binding.target,value);
      }
      if(typeof window.film?.frame!=='function')throw Error('window.film.frame(t) is required');
      if(typeof film.ready==='function')await film.ready();else await film.ready;
      await document.fonts.ready;
      filmHost.time(t);await film.frame(t);filmHost.settle(t);
      await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
      if(templateFailure)throw Error(templateFailure);
      parent.postMessage({kind:'drawn',ticket},'*');
    }catch(e){parent.postMessage({kind:'drawn',ticket,error:String(e.message)},'*')}
  });
  parent.postMessage({kind:'loaded'},'*');
  </script>'''
    return before + definition.source + after
