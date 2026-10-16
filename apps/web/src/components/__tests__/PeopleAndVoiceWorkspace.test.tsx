import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import {act,fireEvent,render,screen,waitFor,cleanup} from "@testing-library/react";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
const api=vi.hoisted(()=>({listPersonProfiles:vi.fn(),listVoiceProfiles:vi.fn(),getDefaultVoice:vi.fn(),listPronunciationEntries:vi.fn(),listAuthorizations:vi.fn(),listMediaPreviews:vi.fn(),listPersonProfileVersions:vi.fn(),listVoiceProfileVersions:vi.fn(),getPersonProfile:vi.fn(),getVoiceProfile:vi.fn(),getReferenceMaterial:vi.fn(),listReferenceMaterials:vi.fn(),savePersonProfile:vi.fn(),saveVoiceProfile:vi.fn(),setDefaultVoice:vi.fn(),importVoiceReference:vi.fn(),createVoicePreview:vi.fn(),createAvatarLipsyncPreview:vi.fn(),scanReferenceMaterials:vi.fn(),getMediaCapability:vi.fn(),cancelPreview:vi.fn(),savePronunciationEntry:vi.fn(),mediaProfilesErrorMessage:vi.fn(cause=>cause instanceof Error?cause.message:"请求失败")}));
vi.mock("@/lib/media-profiles-api",async importOriginal=>({...await importOriginal<typeof import("@/lib/media-profiles-api")>(),...api}));
const referenceProfile=vi.hoisted(()=>vi.fn());
vi.mock("@/lib/assistant-reference",async importOriginal=>({...await importOriginal<typeof import("@/lib/assistant-reference")>(),referenceAssistantProfile:referenceProfile}));
const navigation=vi.hoisted(()=>({query:""}));
vi.mock("next/navigation",()=>({useSearchParams:()=>new URLSearchParams(navigation.query)}));
const intake=vi.hoisted(()=>({getMaterialIntakeStatus:vi.fn(),bindMaterialIntake:vi.fn()}));
vi.mock("@/lib/api",()=>intake);
const importer=vi.hoisted(()=>vi.fn());
vi.mock("@/lib/directoryPicker",()=>({importLocalMaterials:importer}));
vi.mock("@/components/AppShell",()=>({AppShell:({children}:{children:React.ReactNode})=><>{children}</>}));
import {PeopleAndVoiceWorkspace} from "../PeopleAndVoiceWorkspace";
import {MediaProfilesApiError} from "@/lib/media-profiles-api";
import {assistantReferenceHref} from "@/lib/assistant-reference";
const person={id:"person",version:"p1",display_name:"人物",authorization:null,current_authorization:null,runnable:true,reference_url:"/reference/person",reference:{asset_id:"visual"},use_state:"available"};
const voice={id:"voice",version:"v1",display_name:"独立声音",person_profile_id:"",person_version:"",authorization:null,current_authorization:null,runnable:true,reference_url:"/reference/voice",reference:{asset_id:"audio"},use_state:"available",reference_audio_path:"workspace://saved.wav",reference_text:"参考原句",model_id:"model",language:"zh"};
const audio={id:"audio",file_name:"合成声音.wav",media_type:"audio",workspace_reference:"workspace://audio.wav",duration_seconds:2,segments:[],use_state:"available"};
const visual={...audio,id:"visual",file_name:"合成照片.png",media_type:"image",workspace_reference:"workspace://portrait.png",duration_seconds:null};
beforeEach(()=>{navigation.query="";});
it("opens the frozen version from the durable navigation hint again after remount",async()=>{
  navigation.query=assistantReferenceHref({kind:"voice",profile_id:"voice",version:"v0",label:"旧版本声音"}).split("?")[1];
  const first=render(<PeopleAndVoiceWorkspace/>);
  await waitFor(()=>expect(screen.getByLabelText("档案版本")).toHaveAttribute("data-selected-value","v0"));
  expect(api.getVoiceProfile).toHaveBeenCalledWith("voice","v0");
  first.unmount();
  render(<PeopleAndVoiceWorkspace/>);
  await waitFor(()=>expect(screen.getByLabelText("档案版本")).toHaveAttribute("data-selected-value","v0"));
  expect(api.getVoiceProfile.mock.calls.filter(([id,version])=>id==="voice"&&version==="v0")).toHaveLength(2);
  expect(referenceProfile).not.toHaveBeenCalled();
});
it.each([[404,"引用的档案版本不存在或已不可访问"],[403,"当前成员无权查看这个档案版本"]] as const)("reports locator access failure %s without opening a latest-version fallback",async(status,message)=>{
  navigation.query=assistantReferenceHref({kind:"person",profile_id:"unavailable",version:"p-old",label:"不可用档案"}).split("?")[1];
  api.getPersonProfile.mockRejectedValue(new MediaProfilesApiError(status,"server diagnostic"));
  render(<PeopleAndVoiceWorkspace/>);
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(api.getPersonProfile).toHaveBeenCalledWith("unavailable","p-old");
  expect(screen.queryByRole("heading",{name:"人物"})).not.toBeInTheDocument();
  expect(referenceProfile).not.toHaveBeenCalled();
});
it("rejects a profile navigation hint without a frozen version before reading the object",async()=>{
  navigation.query=new URLSearchParams({locate_ref:JSON.stringify({kind:"voice",profile_id:"voice",label:"无版本"})}).toString();
  render(<PeopleAndVoiceWorkspace/>);
  expect(await screen.findByRole("alert")).toHaveTextContent("档案引用缺少固定版本或标识");
  expect(api.getVoiceProfile).not.toHaveBeenCalled();
});
it("locates a frozen voice version from the conversation without replacing its reference",async()=>{
  render(<PeopleAndVoiceWorkspace/>);
  await waitFor(()=>expect(api.listVoiceProfiles).toHaveBeenCalled());
  await act(async()=>{window.dispatchEvent(new CustomEvent("yaohe:assistant-locate-reference",{detail:{kind:"voice",profile_id:"voice",version:"v0",label:"旧版本声音"}}));});
  await screen.findByRole("heading",{name:"独立声音"});
  expect(api.getVoiceProfile).toHaveBeenCalledWith("voice","v0");
  expect(screen.getByLabelText("档案版本")).toHaveAttribute("data-selected-value","v0");
  expect(referenceProfile).not.toHaveBeenCalled();
});
beforeEach(()=>{vi.resetAllMocks();api.listPersonProfiles.mockResolvedValue({items:[person]});api.listVoiceProfiles.mockResolvedValue({items:[voice]});api.getDefaultVoice.mockResolvedValue(voice);api.listPronunciationEntries.mockResolvedValue({items:[]});api.listAuthorizations.mockResolvedValue({items:[]});api.listMediaPreviews.mockResolvedValue({items:[]});api.listPersonProfileVersions.mockResolvedValue({items:[person]});api.listVoiceProfileVersions.mockResolvedValue({items:[voice,{...voice,version:"v0"}]});api.getVoiceProfile.mockImplementation((id,version)=>Promise.resolve({...voice,id,version}));api.getPersonProfile.mockImplementation((id,version)=>Promise.resolve({...person,id,version}));api.getReferenceMaterial.mockImplementation(id=>Promise.resolve(id==="audio"?audio:visual));api.listReferenceMaterials.mockResolvedValue({assets:[audio,visual],total_count:2});api.getMediaCapability.mockResolvedValue({available:true,model_id:null,message:"未配置模型"});vi.stubGlobal("URL",{...URL,createObjectURL:vi.fn(()=>"blob:synthetic-audio"),revokeObjectURL:vi.fn()});api.mediaProfilesErrorMessage.mockImplementation(cause=>cause instanceof Error?cause.message:"请求失败");});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
async function openProfile(name:string) { fireEvent.click(await screen.findByRole("button",{name:new RegExp(`^${name}.*参考`)}));await screen.findByRole("heading",{name}); }
describe("independent profile consumers",()=>{
 it("registers a new person from a real asset reference without proof, person or model fields",async()=>{
  api.savePersonProfile.mockResolvedValue({...person,id:"new-person",version:"p2"});render(<PeopleAndVoiceWorkspace/>);await openProfile("人物");fireEvent.click(screen.getByRole("button",{name:"新增人物"}));fireEvent.change(screen.getByLabelText("名称"),{target:{value:"新人物"}});await inspectSelectOptions(screen.getByLabelText("参考素材"),()=>screen.findByRole("option",{name:"合成照片.png"}));await chooseSelectValue(screen.getByLabelText("参考素材"), "visual");fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await waitFor(()=>expect(api.savePersonProfile).toHaveBeenCalledTimes(1));const input=api.savePersonProfile.mock.calls[0][0];expect(input.id).not.toBe(person.id);expect(input).toMatchObject({display_name:"新人物",avatar_reference:visual.workspace_reference,reference:{asset_id:"visual"},use_state:"available"});expect(input).not.toHaveProperty("authorization");expect(input).not.toHaveProperty("model_id");
 });
 it("the settings voice entrance imports bytes before saving the same profile contract without a model",async()=>{
  api.importVoiceReference.mockResolvedValue({reference:"workspace://imported.wav"});api.saveVoiceProfile.mockResolvedValue({...voice,id:"new-voice",version:"v2"});render(<PeopleAndVoiceWorkspace initialView="voices" embedded/>);await openProfile("独立声音");fireEvent.click(screen.getByRole("button",{name:"新增声音"}));fireEvent.change(screen.getByLabelText("名称"),{target:{value:"第二声音"}});fireEvent.change(screen.getByLabelText("导入声音WAV"),{target:{files:[new File(["synthetic"],"input.wav",{type:"audio/wav"})]}});await screen.findByText("已登记导入声音，尚未生成模型结果。");fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await waitFor(()=>expect(api.saveVoiceProfile).toHaveBeenCalledTimes(1));expect(api.importVoiceReference).toHaveBeenCalledTimes(1);expect(api.saveVoiceProfile.mock.calls[0][0]).toMatchObject({reference_audio_path:"workspace://imported.wav",model_id:"",reference_text:""});expect(api.saveVoiceProfile.mock.calls[0][0]).not.toHaveProperty("person_profile_id");
 });
 it("reopens an exact historical voice and pins that version as default",async()=>{
  api.setDefaultVoice.mockResolvedValue({...voice,version:"v0"});render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");await inspectSelectOptions(screen.getByLabelText("档案版本"),()=>screen.findByRole("option",{name:"第1版"}));await chooseSelectValue(screen.getByLabelText("档案版本"), "v0");await waitFor(()=>expect(api.getVoiceProfile).toHaveBeenCalledWith("voice","v0"));fireEvent.click(await screen.findByRole("button",{name:"设为默认声音"}));await waitFor(()=>expect(api.setDefaultVoice).toHaveBeenCalledWith("voice","v0"));expect(screen.getByLabelText("原始声音参考")).toHaveAttribute("src","/reference/voice");
 });
 it("queued is not a generated audio and can be explicitly cancelled",async()=>{
  const run={id:"r",identity:"i",preview_type:"voice",voice_profile_id:"voice",voice_version:"v1",status:"queued",output_url:null};api.listMediaPreviews.mockResolvedValue({items:[run]});render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");await screen.findByText("等待设备许可");expect(screen.getByRole("button",{name:"生成短试听"})).toBeDisabled();expect(document.querySelectorAll('audio[src]')).toHaveLength(1);fireEvent.click(screen.getByRole("button",{name:"取消预览"}));await waitFor(()=>expect(api.cancelPreview).toHaveBeenCalledWith(run));
 });
 it("person preview selects an independent voice version and uses only its succeeded run",async()=>{
  const run={id:"r",identity:"i",preview_type:"voice",voice_profile_id:"voice",voice_version:"v1",status:"succeeded"};api.listMediaPreviews.mockResolvedValue({items:[run]});api.getMediaCapability.mockResolvedValue({available:true,model_id:"avatar-model"});render(<PeopleAndVoiceWorkspace/>);await openProfile("人物");fireEvent.click(screen.getByRole("button",{name:"制作人物短预览"}));await waitFor(()=>expect(api.createAvatarLipsyncPreview).toHaveBeenCalledWith(expect.objectContaining({person_profile_id:"person",person_version:"p1",voice_profile_id:"voice",voice_version:"v1",voice_preview_identity:"i",authorization_id:null,authorization_version:null,model_id:"avatar-model"})));
 });
 it("save failure keeps the selected source and original draft",async()=>{
  api.savePersonProfile.mockRejectedValue(new Error("synthetic save failed"));render(<PeopleAndVoiceWorkspace/>);await openProfile("人物");fireEvent.click(screen.getByRole("button",{name:"新增人物"}));fireEvent.change(screen.getByLabelText("名称"),{target:{value:"保留输入"}});await inspectSelectOptions(screen.getByLabelText("参考素材"),()=>screen.findByRole("option",{name:"合成照片.png"}));await chooseSelectValue(screen.getByLabelText("参考素材"), "visual");fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await screen.findByText("synthetic save failed");expect(screen.getByLabelText("名称")).toHaveValue("保留输入");expect(screen.getByLabelText("参考素材")).toHaveAttribute("data-selected-value", "visual");
 });
 it("saving a reading replacement does not audition or alter script; a short audition is explicit",async()=>{
  api.savePronunciationEntry.mockResolvedValue({});render(<PeopleAndVoiceWorkspace/>);await openProfile("人物");fireEvent.click(screen.getByRole("button",{name:"朗读替换"}));expect(screen.getByText(/原文仍用于文案和字幕/)).toBeInTheDocument();fireEvent.change(screen.getByLabelText("词语"),{target:{value:"AI"}});fireEvent.change(screen.getByLabelText("朗读文字"),{target:{value:"诶艾"}});fireEvent.submit(screen.getByLabelText("词语").closest("form")!);await waitFor(()=>expect(api.savePronunciationEntry).toHaveBeenCalledWith(expect.objectContaining({term:"AI",target_pronunciation:"诶艾",scope:"workspace"})));expect(api.createVoicePreview).not.toHaveBeenCalled();fireEvent.click(screen.getByRole("button",{name:"声音"}));await openProfile("独立声音");fireEvent.change(await screen.findByLabelText("短试听文字"),{target:{value:"这是一条AI演示。"}});fireEvent.click(screen.getByRole("button",{name:"生成短试听"}));await waitFor(()=>expect(api.createVoicePreview).toHaveBeenCalledWith(expect.objectContaining({voice_profile_id:"voice",voice_version:"v1",preview_text:"这是一条AI演示。"})));
 });
 it("a late old save updates the list without closing a newly opened editor or replacing its draft",async()=>{
  let resolveSave!:(value:unknown)=>void;api.savePersonProfile.mockReturnValue(new Promise(resolve=>{resolveSave=resolve;}));render(<PeopleAndVoiceWorkspace/>);await openProfile("人物");fireEvent.click(screen.getByRole("button",{name:"新增人物"}));fireEvent.change(screen.getByLabelText("名称"),{target:{value:"旧保存"}});await inspectSelectOptions(screen.getByLabelText("参考素材"),()=>screen.findByRole("option",{name:"合成照片.png"}));await chooseSelectValue(screen.getByLabelText("参考素材"), "visual");fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await waitFor(()=>expect(api.savePersonProfile).toHaveBeenCalledTimes(1));fireEvent.click(screen.getByRole("button",{name:"关闭卡片"}));fireEvent.click(screen.getByRole("button",{name:"新增人物"}));fireEvent.change(screen.getByLabelText("名称"),{target:{value:"新的未保存输入"}});await act(async()=>{resolveSave({...person,id:"old-created",display_name:"旧保存",version:"p2"});});expect(screen.getByLabelText("名称")).toHaveValue("新的未保存输入");expect(screen.getByRole("heading",{name:"登记人物"})).toBeInTheDocument();
 });
 it("refresh rereads the selected fixed version and reports a lost reference without generating",async()=>{
  render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");api.getVoiceProfile.mockResolvedValue({...voice,reference_url:null,runnable:false,reference_issue:"源文件失联",runnable_reason:"source_missing"});fireEvent.click(screen.getByRole("button",{name:"刷新列表"}));await screen.findByText("源文件失联");expect(api.getVoiceProfile).toHaveBeenCalledWith("voice","v1");expect(screen.getByText(/当前参考不能用于生成/)).toBeInTheDocument();expect(api.createVoicePreview).not.toHaveBeenCalled();expect(screen.getByText("source_missing").closest("details")).not.toHaveAttribute("open");
 });
 it("a new version retains its actual source range, historical person pairing and explicit usage limit",async()=>{
  const linked={...voice,person_profile_id:"historical-person",person_version:"p0",use_state:"not_for_automation",reference:{asset_id:"audio",start_seconds:0.5,end_seconds:1.5}};api.listVoiceProfiles.mockResolvedValue({items:[linked]});api.saveVoiceProfile.mockResolvedValue({...linked,version:"v2"});render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");fireEvent.click(screen.getByRole("button",{name:"编辑参考"}));await waitFor(()=>expect(screen.getByLabelText("参考素材")).toHaveAttribute("data-selected-value", "audio"));expect(screen.getByLabelText("参考开始秒")).toHaveValue(0.5);fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await waitFor(()=>expect(api.saveVoiceProfile).toHaveBeenCalledWith(expect.objectContaining({id:"voice",person_profile_id:"historical-person",person_version:"p0",use_state:"not_for_automation",reference:{asset_id:"audio",segment_id:null,start_seconds:0.5,end_seconds:1.5}})));
 });

 it("retains a standalone imported WAV when editing its text without choosing a new file",async()=>{
  const standalone={...voice,reference:null};api.listVoiceProfiles.mockResolvedValue({items:[standalone]});api.saveVoiceProfile.mockResolvedValue({...standalone,version:"v2"});render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");fireEvent.click(screen.getByRole("button",{name:"编辑参考"}));fireEvent.change(screen.getByLabelText("参考文字（生成前填写）"),{target:{value:"补齐真实样本文字"}});fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await waitFor(()=>expect(api.saveVoiceProfile).toHaveBeenCalledWith(expect.objectContaining({id:"voice",reference_audio_path:"workspace://saved.wav",reference_text:"补齐真实样本文字"})));expect(api.importVoiceReference).not.toHaveBeenCalled();
 });
 it("imports a person through the existing desktop intake, selects its registered asset and previews the same range",async()=>{
  const video={...visual,id:"video",file_name:"new.mp4",media_type:"video",path:"/isolated-fixtures/new.mp4",workspace_reference:"workspace://new.mp4",duration_seconds:3,segments:[]};importer.mockResolvedValue({directory:"/isolated-fixtures",copied:[{sourceName:"new.mp4",fileName:"new.mp4"}]});api.scanReferenceMaterials.mockResolvedValue({assets:[video]});api.listReferenceMaterials.mockResolvedValue({assets:[video],total_count:1});api.savePersonProfile.mockResolvedValue({...person,reference:{asset_id:"video",start_seconds:1,end_seconds:2}});render(<PeopleAndVoiceWorkspace/>);await openProfile("人物");fireEvent.click(screen.getByRole("button",{name:"新增人物"}));fireEvent.change(screen.getByLabelText("名称"),{target:{value:"自有测试人物"}});fireEvent.click(screen.getByRole("button",{name:"导入人物视频"}));await waitFor(()=>expect(screen.getByLabelText("参考素材")).toHaveAttribute("data-selected-value", "video"));expect(screen.getByLabelText("本次人物参考")).toHaveAttribute("src","/api/production/materials/video/media");fireEvent.change(screen.getByLabelText("参考开始秒"),{target:{value:"1"}});fireEvent.change(screen.getByLabelText("参考结束秒"),{target:{value:"2"}});fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await waitFor(()=>expect(api.savePersonProfile).toHaveBeenCalledWith(expect.objectContaining({reference:{asset_id:"video",segment_id:null,start_seconds:1,end_seconds:2}})));expect(importer).toHaveBeenCalledTimes(1);expect(api.scanReferenceMaterials).toHaveBeenCalledTimes(1);
 });
 it("rejects an empty range without losing the draft or calling save",async()=>{
  render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");fireEvent.click(screen.getByRole("button",{name:"编辑参考"}));await waitFor(()=>expect(screen.getByLabelText("参考素材")).toHaveAttribute("data-selected-value", "audio"));fireEvent.change(screen.getByLabelText("参考开始秒"),{target:{value:"1"}});fireEvent.change(screen.getByLabelText("参考结束秒"),{target:{value:"1"}});fireEvent.submit(screen.getByLabelText("名称").closest("form")!);await screen.findByText(/结束时间需晚于开始时间/);expect(api.saveVoiceProfile).not.toHaveBeenCalled();expect(screen.getByLabelText("名称")).toHaveValue("独立声音");
 });
 it("separates unavailable models from usable references and offers a repair route and task return",async()=>{
  api.getMediaCapability.mockResolvedValue({available:false,model_id:null,message:"未安装声音模型"});render(<PeopleAndVoiceWorkspace initialView="voices" returnTaskId="task-1"/>);await openProfile("独立声音");await screen.findByText(/参考已保存，暂不可生成/);expect(screen.getByRole("button",{name:"生成短试听"})).toBeDisabled();expect(screen.getByRole("link",{name:"查看运行条件"})).toHaveAttribute("href","/health");expect(screen.getByRole("link",{name:"返回当前任务"})).toHaveAttribute("href","/tasks/task-1");expect(screen.getByLabelText("原始声音参考")).toHaveAttribute("src","/reference/voice");
 });

 it("preserves the person draft when native import needs the real intake binding, and binds through its existing API",async()=>{
  importer.mockResolvedValue({error:"当前工作区还没有接入素材目录，请先接入后再导入。"});const status={bound_to_current_workspace:false,binding_available:true,directory_state:"ready",directory_message:"素材目录可访问，等待关联到当前工作区。",configured_directory:"/isolated-fixtures/inbox"};intake.getMaterialIntakeStatus.mockResolvedValue(status);intake.bindMaterialIntake.mockResolvedValue({...status,bound_to_current_workspace:true});render(<PeopleAndVoiceWorkspace/>);await openProfile("人物");fireEvent.click(screen.getByRole("button",{name:"新增人物"}));fireEvent.change(screen.getByLabelText("名称"),{target:{value:"保留未完成的人物草稿"}});fireEvent.click(screen.getByRole("button",{name:"导入人物视频"}));await screen.findByText("当前工作区还没有接入素材目录，请先接入后再导入。");fireEvent.click(screen.getByRole("button",{name:"接入素材目录"}));fireEvent.click(await screen.findByRole("button",{name:"关联到当前工作区"}));await waitFor(()=>expect(intake.bindMaterialIntake).toHaveBeenCalledWith("/isolated-fixtures/inbox"));expect(screen.getByLabelText("名称")).toHaveValue("保留未完成的人物草稿");expect(api.scanReferenceMaterials).not.toHaveBeenCalled();
 });

 it("references the exact historical profile version without inventing an asset selection",async()=>{
  render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");fireEvent.click(screen.getByRole("button",{name:"引用当前档案到对话"}));expect(referenceProfile).toHaveBeenLastCalledWith({kind:"voice",profile_id:"voice",version:"v1",label:"独立声音"},true);await chooseSelectValue(screen.getByLabelText("档案版本"), "v0");await waitFor(()=>expect(referenceProfile).toHaveBeenLastCalledWith({kind:"voice",profile_id:"voice",version:"v0",label:"独立声音"}));expect(referenceProfile.mock.calls.at(-1)![0]).not.toHaveProperty("asset_id");
 });

 it("shows the persisted audition text separately from newly edited input and keeps its output identity",async()=>{
  api.listMediaPreviews.mockResolvedValue({items:[{id:"r",identity:"old-audition",preview_type:"voice",voice_profile_id:"voice",voice_version:"v1",status:"succeeded",preview_text:"真实生成的旧句子",output_url:"/generated/old.wav"}]});render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");await screen.findByText("这次结果的试听文字：真实生成的旧句子");fireEvent.change(screen.getByLabelText("短试听文字"),{target:{value:"这句还没有生成"}});expect(screen.getByText(/当前输入与这条结果不同/)).toBeInTheDocument();expect(document.querySelector('audio[src="/generated/old.wav"]')).toBeInTheDocument();expect(api.createVoicePreview).not.toHaveBeenCalled();
 });

});

it("keeps the full resource-budget failure in folded diagnostics beside an actionable summary",async()=>{
 api.createVoicePreview.mockRejectedValueOnce(new Error("CPU渲染预算尚未标定：缺少 DEVICE_CPU_RENDER_MAX_DURATION_SECONDS"));
 render(<PeopleAndVoiceWorkspace initialView="voices"/>);await openProfile("独立声音");fireEvent.click(screen.getByRole("button",{name:"生成短试听"}));
 expect(await screen.findByRole("alert")).toHaveTextContent("本机生成资源限额尚未配置，参考已保存");
 expect(screen.getByRole("link",{name:"查看运行条件"})).toHaveAttribute("href","/health");
 expect(screen.getByText(/缺少 DEVICE_CPU_RENDER/).closest("details")).not.toHaveAttribute("open");
});
