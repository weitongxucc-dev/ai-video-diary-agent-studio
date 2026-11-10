import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import type {VideoTemplate} from '@/lib/types';
import {VisualTemplateLibraryEditor} from '../VisualTemplateEditor';
import {GraphicOverlayEditor} from '../GraphicOverlayEditor';
import type {VisualTemplateOverlay} from '@/lib/types/timeline';
const api=vi.hoisted(()=>({copy:vi.fn(),read:vi.fn(),update:vi.fn()}));
vi.mock('@/lib/video-template-api',()=>({saveVisualTemplateCopy:api.copy,readVideoTemplateVersion:api.read,
  createVideoTemplateVersion:api.update,readVisualTemplates:vi.fn(),VideoTemplateRequestError:class extends Error{}}));
vi.mock('../VisualTemplatePreview',()=>({VisualTemplatePreview:()=> <div>真实预览在桌面验收</div>}));
const template:VideoTemplate={id:'builtin',version:1,name:'姓名条',origin:'built_in',visibility:'shared',
  source_task_id:null,source_timeline_version_id:null,created_at:'2026-11-10',settings:{kind:'visual_component',
    language:'zh-CN',aspect_ratio:'9:16',duration_target_seconds:60,style_instruction:'',fit_mode:'fill',subtitle_style:{},voice:null,
    visual:{engine:'openfilm-html-v1',category:'title',width:1200,height:300,duration_seconds:4,
      source:'window.film={frame(t){}}',bindings:[{key:'name',label:'姓名',kind:'text',target:'.name',default:'示例姓名'}],
      values:{},dependencies:[],provenance:[{repository:'https://github.com/openfilm/openfilm',revision:'fixed',path:'name.html',license:'CC0-1.0',source_sha256:'a'.repeat(64)}]}}};
beforeEach(()=>vi.clearAllMocks());
it('opens the selected animation and previews its current parameters without a blank title editor',()=>{
  const media=vi.fn();
  const layer:VisualTemplateOverlay={kind:'template',id:'animation',name:'我的动画',
    template_id:'mine',template_version:2,definition:template.settings.visual!,start_seconds:1,end_seconds:4,
    x:.5,y:.65,width:.8,opacity:1,z_index:0,keyframes:[],animation_offset_seconds:0};
  render(<GraphicOverlayEditor taskId="task" items={[layer]} selectedId={layer.id} duration={5}
    busy={false} onPreview={vi.fn()} onMediaPreview={media} onDraftChange={vi.fn()}/>);
  expect(screen.getByText('我的动画 · 来源第2版')).toBeVisible();
  expect(screen.queryByLabelText('标题文字')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('姓名'),{target:{value:'本次人物'}});
  fireEvent.click(screen.getByRole('button',{name:'运行动画预览'}));
  expect(media).toHaveBeenCalledWith(expect.objectContaining({overlay_action:'update',overlay_id:layer.id,
    graphic_overlay:expect.objectContaining({y:.65,definition:expect.objectContaining({values:{name:'本次人物'}})})}));
});
it('saves human parameter and source changes with the original fixed provenance',async()=>{
  const saved=vi.fn();api.copy.mockResolvedValue({...template,id:'mine',origin:'user'});
  render(<VisualTemplateLibraryEditor template={template} onSaved={saved}/>);
  fireEvent.change(screen.getByLabelText('姓名'),{target:{value:'中文嘉宾姓名'}});
  fireEvent.change(screen.getByLabelText('模板动画源码'),{target:{value:'window.film={frame(t){/* revised animation */}}'}});
  fireEvent.click(screen.getByRole('button',{name:'另存我的视觉模板'}));
  await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(api.copy).toHaveBeenCalledWith(expect.objectContaining({source_template_id:'builtin',source_template_version:1,
    visual:expect.objectContaining({values:{name:'中文嘉宾姓名'},source:expect.stringContaining('revised animation'),
      provenance:template.settings.visual!.provenance})}));
});
it('repairs an explicit invalid dependency declaration while preserving the source',async()=>{
  const saved=vi.fn();api.copy.mockResolvedValue({...template,id:'mine',origin:'user'});
  render(<VisualTemplateLibraryEditor template={{...template,settings:{...template.settings,
    visual:{...template.settings.visual!,dependencies:['']}}}} onSaved={saved}/>);
  fireEvent.change(screen.getByLabelText('模板依赖声明'),{target:{value:'[]'}});
  fireEvent.click(screen.getByRole('button',{name:'采用依赖声明草稿',hidden:true}));
  fireEvent.click(screen.getByRole('button',{name:'另存我的视觉模板'}));
  await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(api.copy).toHaveBeenCalledWith(expect.objectContaining({visual:expect.objectContaining({
    dependencies:[],source:template.settings.visual!.source,provenance:template.settings.visual!.provenance})}));
});
it('queries the original version after a lost reply without replaying the write',async()=>{
  const saved=vi.fn();api.copy.mockRejectedValue(Error('reply lost'));
  render(<VisualTemplateLibraryEditor template={template} onSaved={saved}/>);
  fireEvent.click(screen.getByRole('button',{name:'另存我的视觉模板'}));
  await screen.findByRole('alert');
  expect(screen.getByRole('button',{name:'另存我的视觉模板'})).toBeDisabled();
  const submitted=api.copy.mock.calls[0][0];
  api.read.mockResolvedValue({...template,id:`template_${submitted.request_id.replaceAll('-','')}`,
    origin:'user',can_edit:true});
  fireEvent.click(screen.getByRole('button',{name:/查询原保存结果/}));
  await waitFor(()=>expect(saved).toHaveBeenCalled());
  expect(api.copy).toHaveBeenCalledTimes(1);
  expect(api.read).toHaveBeenCalledWith(`template_${submitted.request_id.replaceAll('-','')}`,1);
});
