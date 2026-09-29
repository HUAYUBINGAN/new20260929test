/* ============================================================
 * ui_smoke.js —— 界面回归冒烟测试
 *
 * 在 Node 里用 DOM 桩加载 ui/index.html 的 <script>，跑真实交互路径。
 * 目的：`node --check` 只能验语法，验不出「函数名打错 / 元素 id 写错 /
 * 引用不存在的元素 / 状态没重置」这类运行时问题。这个测试补的就是这块。
 *
 * 用法：  node tests/ui_smoke.js
 * 退出码：0 = 全过；1 = 有失败；2 = 测试自身无法运行（如找不到 script 块）
 * ============================================================ */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
/* 默认测 ui/index.html；可用环境变量 UI_HTML 指定别的文件
   （用于变异测试：故意改坏一份副本，确认这套用例真的能报错） */
const HTML_PATH = process.env.UI_HTML
  ? path.resolve(process.env.UI_HTML)
  : path.join(ROOT, 'ui', 'index.html');

/* ---------- 断言框架 ---------- */
let pass = 0;
const failures = [];
function check(name, ok, extra) {
  if (ok) {
    pass++;
    console.log(`  [PASS] ${name}`);
  } else {
    failures.push(name);
    console.log(`  [FAIL] ${name}${extra !== undefined ? '   -> ' + extra : ''}`);
  }
}
const section = t => console.log(`\n${t}`);

/* ---------- 读取被测文件 ---------- */
if (!fs.existsSync(HTML_PATH)) {
  console.error(`找不到界面文件：${HTML_PATH}`);
  process.exit(2);
}
const html = fs.readFileSync(HTML_PATH, 'utf8');

/* HTML 里真实存在的 id 集合 —— 用作「假元素」的守门人 */
const validIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
/* HTML 里声明的 CSS 变量 */
const declaredVars = new Set([...html.matchAll(/(--[a-z0-9-]+)\s*:/g)].map(m => m[1]));

const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  console.error('index.html 里找不到 <script> 块');
  process.exit(2);
}
const code = scriptMatch[1];

/* ---------- DOM 桩 ---------- */
const els = Object.create(null);

function makeEl(tag, id) {
  const cls = new Set();
  const el = {
    tagName: tag,
    id: id || '',
    innerHTML: '',
    textContent: '',
    value: '',
    checked: false,
    disabled: false,
    style: {},
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    _ev: Object.create(null),
    _clicked: 0,
    _scrolled: 0,
    classList: {
      add: (...c) => c.forEach(x => cls.add(x)),
      remove: (...c) => c.forEach(x => cls.delete(x)),
      contains: c => cls.has(c),
      toggle: c => (cls.has(c) ? cls.delete(c) : cls.add(c)),
      _all: cls,
    },
    addEventListener(ev, fn) { (el._ev[ev] = el._ev[ev] || []).push(fn); },
    dispatch(ev, o) { (el._ev[ev] || []).forEach(fn => fn(o)); },
    click() { el._clicked++; },
    scrollIntoView() { el._scrolled++; },
    querySelector(sel) { return el._inner || (el._inner = makeEl(sel, '')); },
  };
  return el;
}

/* 关键守门：id 必须在 HTML 里真实存在，否则说明 JS 引用了一个不存在的元素 */
function getEl(id) {
  if (!validIds.has(id)) {
    throw new Error(`DOM 桩拦截：index.html 里不存在 id="${id}"（JS 引用了一个不存在的元素）`);
  }
  return els[id] || (els[id] = makeEl('div', id));
}

/* ---------- 环境与接口桩 ---------- */
const CALLS = [];
let routes = Object.create(null);
let confirmAnswer = true;
let timers = [];
let rejections = [];

const ENV = {
  cli_ok: true,
  ffmpeg_ok: true,
  gpu_ok: true,
  ffmpeg: 'D:\\ComfyUI\\ComfyUI_windows_portable-G314\\ffmpeg.exe',
  ffmpeg_name: 'ffmpeg.exe',
  cuda: ['C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v13.3\\bin\\x64'],
  models: ['models/ggml-large-v3.bin'],
};
const CFG = {
  model: 'models/ggml-large-v3.bin', language: 'zh', format: 'srt',
  threads: 16, use_gpu: true, vad: true, prompt: '',
};

routes['/api/status'] = { env: ENV, config: CFG };
routes['/api/cache_info'] = { total: 3145728, tmp: { count: 3 }, output: { count: 2 } };
routes['/api/progress'] = { stage: 'idle', running: false, progress: 0, message: '待命', log: [] };

async function fetchStub(url, opt) {
  const u = String(url);
  opt = opt || {};
  CALLS.push({ url: u, method: opt.method || 'GET', body: opt.body, headers: opt.headers || {} });
  const h = routes[u];
  if (h === undefined) throw new Error('fetch 桩未配置路由: ' + u);
  return { json: async () => (typeof h === 'function' ? h(opt) : h) };
}

const sandbox = {
  console,
  document: { getElementById: getEl },
  fetch: fetchStub,
  confirm: () => confirmAnswer,
  location: { href: '' },
  setTimeout: fn => { timers.push(fn); return timers.length; },   // 不真正执行，避免 poll 无限递归
  clearTimeout: () => {},
};

process.on('unhandledRejection', e => rejections.push(e));

/* ---------- 加载被测脚本 ---------- */
const ctx = vm.createContext(sandbox);
try {
  /* 末尾附加一行探针：把 const/let 声明的词法绑定也暴露出来
     （script 顶层 const 不会挂到全局对象上，必须这样取） */
  vm.runInContext(code + '\n;globalThis.__T = { state, $, flash, fmtSize };',
                  ctx, { filename: 'ui/index.html<script>' });
} catch (e) {
  console.error('加载界面脚本失败：', e.message);
  process.exit(2);
}
const T = ctx.__T;

/* 让 vm 里的 promise 链跑完 */
const flush = (n = 12) => new Promise(res => {
  let i = 0;
  const step = () => (++i >= n ? res() : setImmediate(step));
  setImmediate(step);
});

/* ============================================================
 * 用例
 * ============================================================ */
(async () => {
  console.log('='.repeat(64));
  console.log('界面回归冒烟测试  (ui/index.html)');
  console.log('='.repeat(64));

  await flush();

  section('[1] 初始化 boot()');
  check('探针取到 state', !!T && !!T.state);
  check('envbar 渲染出环境 chip', els.envbar.innerHTML.includes('GPU 加速'),
        els.envbar.innerHTML.slice(0, 80));
  check('ffmpeg chip 显示文件名而非全路径', els.envbar.innerHTML.includes('ffmpeg：ffmpeg.exe'));
  check('CUDA chip 已渲染', els.envbar.innerHTML.includes('CUDA 运行库'));
  check('模型下拉已填充', els.model.innerHTML.includes('ggml-large-v3.bin'));
  check('模型标注了用途', els.model.innerHTML.includes('（准）'));
  check('缓存统计已显示', els.cacheinfo.textContent.startsWith('缓存 '), els.cacheinfo.textContent);
  check('清理/重置按钮已绑定',
        typeof els.clearcache.onclick === 'function' && typeof els.resetcfg.onclick === 'function');
  check('拖拽区 click 已绑定', typeof els.drop.onclick === 'function');
  check('轮询已启动', timers.length >= 1, timers.length);
  check('初始未产生未捕获异常', rejections.length === 0, rejections.map(String).join(' | '));

  section('[2] 选择文件');
  const f1 = { name: 'A.mp4', size: 1234567 };
  ctx.pickFile(f1);
  check('显示文件名', els.pname.textContent === 'A.mp4');
  check('显示文件大小', els.psize.textContent === '1.2 MB', els.psize.textContent);
  check('已选卡片出现', els.picked.style.display === 'flex');
  check('开始按钮可用', els.start.disabled === false);
  check('给出已选提示', els.msg.textContent.includes('文件已选好'), els.msg.textContent);

  section('[3] 回归：移除后重选同一个文件');
  els.file.value = 'C:\\fakepath\\A.mp4';
  els.file.onchange({ target: { files: [f1] } });
  check('第一次选择生效', els.pname.textContent === 'A.mp4');
  els.pclear.onclick();
  check('移除后状态清空', T.state.file === null && T.state.videoPath === '');
  check('移除后 input.value 被重置', els.file.value === '', JSON.stringify(els.file.value));
  check('移除后开始按钮禁用', els.start.disabled === true);
  /* 再点拖拽区。真实场景里此刻 input 内可能仍有旧值（旧版正因如此而静默失效），
     所以这里显式塞回一个旧值，验证「打开选择框前必定清空」这条不变量。 */
  els.file.value = 'C:\\fakepath\\A.mp4';
  els.drop.onclick();
  check('打开选择框前先清空 value（否则再选同一文件无反应）', els.file.value === '',
        JSON.stringify(els.file.value));
  check('选择框已被触发', els.file._clicked >= 1, els.file._clicked);
  els.file.onchange({ target: { files: [f1] } });
  check('再选同一个文件界面有反应', els.pname.textContent === 'A.mp4');

  section('[4] 手填路径优先于上传');
  els.vpath.oninput({ target: { value: 'D:\\video\\x.mp4' } });
  check('state.videoPath 已记录', T.state.videoPath === 'D:\\video\\x.mp4');
  check('已选卡片隐藏', els.picked.style.display === 'none');
  check('开始按钮仍可用', els.start.disabled === false);

  section('[5] 渲染：抽音轨阶段');
  ctx.render({ stage: 'extract', running: true, progress: 0, message: '正在抽取音轨…', log: ['line1'] });
  check('进度条切到不定态', els.bar.classList.contains('indet'));
  check('清掉行内 width（否则动画变满格滑动）', els.bar._inner.style.width === '',
        JSON.stringify(els.bar._inner.style.width));
  check('百分比位显示「抽音轨」', els.pct.textContent === '抽音轨');
  check('取消按钮出现', els.cancel.style.display === 'inline-flex');
  check('维护按钮被禁用', els.clearcache.disabled === true && els.resetcfg.disabled === true);
  check('日志已写入', els.log.textContent.includes('line1'));

  section('[6] 渲染：识别中（真实进度）');
  ctx.render({ stage: 'transcribe', running: true, progress: 53, message: '正在识别… 53%' });
  check('退出不定态', !els.bar.classList.contains('indet'));
  check('宽度 = 53%', els.bar._inner.style.width === '53%', els.bar._inner.style.width);
  check('百分比 = 53%', els.pct.textContent === '53%');

  section('[7] 临时提示不被轮询秒盖（state.hold）');
  T.state.hold = Date.now() + 5000;
  T.state.holdMsg = '自定义提示';
  els.msg.textContent = '自定义提示';
  ctx.render({ stage: 'transcribe', running: true, progress: 10, message: '正在识别… 10%' });
  check('hold 期内不被覆盖', els.msg.textContent === '自定义提示', els.msg.textContent);
  T.state.hold = 0;
  ctx.render({ stage: 'transcribe', running: true, progress: 11, message: '正在识别… 11%' });
  check('hold 过期后恢复接管', els.msg.textContent === '正在识别… 11%', els.msg.textContent);

  section('[8] 渲染：完成 + 载入结果');
  routes['/api/result'] = { ok: true, name: 'A.srt', text: '1\n00:00:00,000 --> 00:00:02,000\n你好世界' };
  T.state.lastStage = '';
  ctx.render({ stage: 'done', running: false, progress: 100, message: '完成！' });
  await flush(8);
  check('百分比 100%', els.pct.textContent === '100%');
  check('宽度 100%', els.bar._inner.style.width === '100%');
  check('结果区展开', els.resultbox.style.display === 'block');
  check('结果文件名已显示', els.rname.textContent === 'A.srt');
  check('结果内容已载入', els.out.textContent.includes('你好世界'), els.out.textContent.slice(0, 30));
  check('开始按钮恢复可用', els.start.disabled === false);
  check('维护按钮恢复可用', els.clearcache.disabled === false);
  check('完成后刷新了缓存统计', CALLS.filter(c => c.url === '/api/cache_info').length >= 2);

  section('[9] 结果超长时截断预览');
  routes['/api/result'] = { ok: true, name: 'big.srt', text: 'x'.repeat(4000) };
  T.state.lastStage = '';
  ctx.render({ stage: 'done', running: false, progress: 100, message: '完成！' });
  await flush(8);
  check('给出截断提示', els.out.textContent.includes('仅预览前 3000 字符'));
  check('预览长度受限', els.out.textContent.length < 3200, els.out.textContent.length);

  section('[10] 渲染：已取消');
  ctx.render({ stage: 'cancelled', running: false, progress: 53, message: '已取消' });
  check('进度归零（不停在 53%）', els.pct.textContent === '0%', els.pct.textContent);
  check('宽度归零', els.bar._inner.style.width === '0%', els.bar._inner.style.width);
  check('提示为已取消', els.msg.textContent === '已取消');

  section('[11] 渲染：错误（多行截断 + 不解析 HTML）');
  ctx.render({ stage: 'error', running: false, progress: 12, message: '出错了',
               error: '抽音轨失败：\n第二行不该显示' });
  check('只显示首行', els.msg.textContent === '抽音轨失败：', els.msg.textContent);
  check('错误样式已加', els.msg.classList.contains('msg-err'));
  ctx.render({ stage: 'transcribe', running: true, progress: 20, message: '正在识别… 20%' });
  check('恢复正常后摘掉错误样式', !els.msg.classList.contains('msg-err'));
  els.msg.innerHTML = 'SENTINEL';
  ctx.showErr('<img src=x onerror=alert(1)>');
  check('错误文本走 textContent，不解析标签',
        els.msg.innerHTML === 'SENTINEL' && els.msg.textContent === '<img src=x onerror=alert(1)>');
  check('showErr 清掉 hold（错误不被提示盖住）', T.state.hold === 0);

  section('[12] 启动任务（含上传）');
  routes['/api/upload'] = { ok: true, path: 'tmp\\B.mp4' };
  routes['/api/start'] = { ok: true };
  T.state.videoPath = '';
  T.state.file = { name: 'B.mp4', size: 2048 };
  els.log.textContent = 'OLD';
  els.resultbox.style.display = 'block';
  await ctx.startJob();
  await flush(10);
  const up = CALLS.filter(c => c.url === '/api/upload').pop();
  check('上传接口已调用', !!up);
  check('上传带 X-Filename 头',
        !!up && up.headers['X-Filename'] === encodeURIComponent('B.mp4'),
        up && up.headers['X-Filename']);
  const st = CALLS.filter(c => c.url === '/api/start').pop();
  check('启动接口已调用', !!st);
  let pl = null;
  try { pl = st ? JSON.parse(st.body) : null; } catch (e) { /* 保留 null */ }
  check('payload 字段齐全',
        !!pl && ['video', 'model', 'language', 'format', 'threads', 'use_gpu', 'vad', 'prompt']
          .every(k => k in pl), st && st.body);
  check('payload.video 用了上传后的路径', !!pl && pl.video === 'tmp\\B.mp4', pl && pl.video);
  check('启动后清空日志', els.log.textContent === '');
  check('启动后结果区收起', els.resultbox.style.display === 'none');
  check('启动后进入运行态',
        els.start.disabled === true && els.cancel.style.display === 'inline-flex');

  section('[13] 取消 / 打开输出目录 / 下载');
  routes['/api/cancel'] = { ok: true };
  routes['/api/open_output'] = { ok: true };
  await els.cancel.onclick();
  await flush(4);
  check('取消接口已调用',
        CALLS.some(c => c.url === '/api/cancel' && c.method === 'POST'));
  els.openout.onclick();
  await flush(4);
  check('打开输出目录已调用', CALLS.some(c => c.url === '/api/open_output'));
  els.rname.textContent = 'A.srt';
  sandbox.location.href = '';
  els.dl.onclick();
  check('下载链接带文件名参数',
        sandbox.location.href === '/api/download?f=A.srt', sandbox.location.href);

  section('[14] 清理缓存');
  confirmAnswer = true;
  routes['/api/cache_info'] = { total: 0, tmp: { count: 0 }, output: { count: 0 } };
  routes['/api/clear_cache'] = opt => ({
    ok: true, freed: 1048576, removed: ['tmp\\B.mp4'],
    targets: JSON.parse(opt.body).targets,
  });
  T.state.videoPath = 'tmp\\B.mp4';
  T.state.file = null;
  els.picked.style.display = 'flex';
  await els.clearcache.onclick();
  await flush(8);
  const cc = CALLS.filter(c => c.url === '/api/clear_cache').pop();
  check('清理接口已调用', !!cc);
  check('清理范围仅 tmp + output',
        !!cc && JSON.parse(cc.body).targets.join(',') === 'tmp,output', cc && cc.body);
  check('删掉当前选中文件的副本后重置界面',
        T.state.videoPath === '' && els.start.disabled === true);
  check('给出明确提示', els.msg.textContent.includes('已清理缓存'), els.msg.textContent);
  check('缓存统计已刷新', els.cacheinfo.textContent.startsWith('缓存 '), els.cacheinfo.textContent);

  section('[15] 恢复默认设置');
  routes['/api/reset_config'] = { ok: true, config: Object.assign({}, CFG) };
  els.language.value = 'en';
  els.vad.checked = false;
  els.threads.value = '4';
  els.prompt.value = '乱七八糟';
  confirmAnswer = true;
  await els.resetcfg.onclick();
  await flush(8);
  check('语言已回默认', els.language.value === 'zh', els.language.value);
  check('VAD 已回默认', els.vad.checked === true);
  check('线程已回默认', els.threads.value === '16', els.threads.value);
  check('提示词已清空', els.prompt.value === '');
  check('已给出提示', els.msg.textContent === '设置已恢复默认', els.msg.textContent);

  section('[16] 静态一致性');
  const usedIds = new Set([...code.matchAll(/\$\('([^']+)'\)/g)].map(m => m[1]));
  const missingIds = [...usedIds].filter(id => !validIds.has(id));
  check(`JS 引用的 ${usedIds.size} 个 id 全部存在于 HTML`,
        missingIds.length === 0, missingIds.join(','));
  const usedVars = new Set([...html.matchAll(/var\((--[a-z0-9-]+)\)/g)].map(m => m[1]));
  const undefVars = [...usedVars].filter(v => !declaredVars.has(v));
  check(`CSS 变量无未定义引用（用了 ${usedVars.size} 个）`,
        undefVars.length === 0, undefVars.join(','));
  check('无 join("\\\\n") 式转义残留（不会产生假换行）', !code.includes("join('\\\\n')"));
  check('无 split("\\\\\\\\") 式转义残留', !code.includes("split('\\\\\\\\')"));
  check('全部用例跑完无未捕获异常',
        rejections.length === 0, rejections.map(String).join(' | '));

  /* ---------- 汇总 ---------- */
  console.log('\n' + '='.repeat(64));
  console.log(`结果：${pass} 通过 / ${failures.length} 失败`);
  if (failures.length) {
    console.log('\n失败项：');
    failures.forEach(f => console.log('  - ' + f));
    console.log('='.repeat(64));
    process.exit(1);
  }
  console.log('界面自检全部通过。');
  console.log('='.repeat(64));
  process.exit(0);
})();
