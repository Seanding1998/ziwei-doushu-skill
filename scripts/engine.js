#!/usr/bin/env node
/**
 * ziwei-doushu / scripts/engine.js
 * iztro 计算引擎 —— 由 Python 调用，读 stdin JSON、写 stdout JSON。
 * 本文件不做解读、不做格式判断，只把 iztro 的确定性数据原样吐出。
 *
 * 输入（stdin JSON）:
 *   { "date": "1990-06-07", "calendar": "solar"|"lunar", "timeIndex": 4,
 *     "gender": "男", "years": [2026, 2027], "fixLeap": true }
 * 输出（stdout JSON）: { ok: true, engine, version, astrolabe: {...} } 或 { ok:false, error, message }
 *
 * 另有**历法查表**模式（不排盘，只回答「这些年里有哪些闰月」）:
 *   输入 { "mode": "leap_months", "years": [1995, 2030] }
 *   输出 { ok:true, mode:"leap_months", leapMonths:[{lunarYear, leapMonth, lengthDays,
 *          picks:[{lunarDay, solarDate, lunarDate}]}] }
 *   用 lunar-typescript 的 LunarYear.getLeapMonth() / Lunar.fromYmd(y, -m, d) —— 一次子进程
 *   查完整个年段，不再按天扫排盘引擎。不传 mode 时走下面的排盘主路径，行为不变。
 *
 * 另有**批量排盘**模式（只给对拍器省子进程启动开销）:
 *   输入 { "mode": "batch", "requests": [ <普通请求>, ... ], "out": "<可选文件路径>" }
 *   输出 { ok:true, mode:"batch", count, results:[ <同形结果>, ... ] }
 *   给了 out 就把整份响应写进该文件、stdout 只留一行确认（大批量落文件，别挤管道）。
 */
'use strict';

let iztro = null;
const errors = [];
for (const spec of ['iztro', 'iztro/lib/index.js']) {
  try { iztro = require(spec); break; } catch (e) { errors.push(String(e.message)); }
}
/**
 * iztro 的可用性**延迟到排盘路径再判**（不在模块顶层 exit）：
 * 历法查表模式（mode: "leap_months"）只用 lunar-typescript，不该被缺失的 iztro 连坐。
 */
function requireIztro() {
  if (iztro) return null;
  return {
    ok: false, error: 'IZTRO_NOT_FOUND',
    message: '未找到 iztro 模块。请先运行: python ziwei.py deps',
    detail: errors,
  };
}

/**
 * 节气四柱（立春分界）—— 由 lunar-lite 提供（iztro 自身的历法依赖），
 * 权威且不必自己近似节气交接时刻。
 */
function jieqiPillar(dateStr, timeIndex) {
  let lunar = null;
  for (const spec of ['lunar-lite']) {
    try { lunar = require(spec); break; } catch (e) { /* next */ }
  }
  if (!lunar || typeof lunar.getHeavenlyStemAndEarthlyBranchBySolarDate !== 'function') return null;
  try {
    const r = lunar.getHeavenlyStemAndEarthlyBranchBySolarDate(dateStr, Number(timeIndex) || 0);
    const j = (x) => (Array.isArray(x) ? x.join('') : String(x));
    return {
      yearly: j(r.yearly), monthly: j(r.monthly), daily: j(r.daily), hourly: j(r.hourly),
      text: [j(r.yearly), j(r.monthly), j(r.daily), j(r.hourly)].join(' '),
    };
  } catch (e) {
    return null;
  }
}

/**
 * 历法库（lunar-typescript）——**只用它查表**，不用它排盘。
 *
 * 注意：这里刻意加载 `lunar-typescript` 本尊，而不是 iztro 依赖树里的 `lunar-lite`：
 * lunar-lite 的 `lib/leap.js` 虽然导出 getLeapMonth/getLeapDays，但该版本取不到
 * LUNAR_INFO，一调用就抛。查表必须走 lunar-typescript。
 */
function loadLunarTypescript() {
  const errs = [];
  for (const spec of ['lunar-typescript', 'lunar-typescript/lib/index.js']) {
    try { return require(spec); } catch (e) { errs.push(String(e && e.message || e)); }
  }
  return { __error: errs };
}

const LUNAR_LIB_HINT =
  '未找到 lunar-typescript。请安装 Node.js 18+ 后运行: python ziwei.py deps ' +
  '（或 npm install lunar-typescript@1.8.6 --prefix <skill 目录>）';

/** 该闰月是 29 天还是 30 天：用 Lunar.fromYmd(y, -m, 30) 试探，不抛即 30，抛即 29。 */
function leapMonthLength(Lunar, y, m) {
  try {
    Lunar.fromYmd(y, -m, 30);
    return 30;
  } catch (e) {
    return 29;
  }
}

/**
 * 历法查表模式：`{ mode: "leap_months", years: [lo, hi] }`。
 * 对每个有闰月的年份，取该闰月 lunarYear / leapMonth / lengthDays，
 * 并给出第 1 天 / 中间一天（ceil(lengthDays/2)）/ 最后一天三个日期。
 */
function runLeapMonths(req) {
  const years = Array.isArray(req.years) ? req.years : [];
  if (years.length !== 2) {
    return { ok: false, error: 'BAD_YEARS', message: 'leap_months 模式需要 years: [起始年, 结束年]' };
  }
  const lo = Number(years[0]);
  const hi = Number(years[1]);
  if (!Number.isInteger(lo) || !Number.isInteger(hi)) {
    return { ok: false, error: 'BAD_YEARS', message: `years 必须是整数年：${JSON.stringify(years)}` };
  }
  const from = Math.min(lo, hi);
  const to = Math.max(lo, hi);

  const lib = loadLunarTypescript();
  if (lib.__error) {
    return { ok: false, error: 'LUNAR_LIB_NOT_FOUND', message: LUNAR_LIB_HINT, detail: lib.__error };
  }
  const { LunarYear, Lunar } = lib;
  if (typeof LunarYear?.fromYear !== 'function' || typeof Lunar?.fromYmd !== 'function') {
    return { ok: false, error: 'LUNAR_LIB_NOT_FOUND', message: LUNAR_LIB_HINT,
             detail: ['lunar-typescript 缺少 LunarYear.fromYear / Lunar.fromYmd'] };
  }

  let version = 'unknown';
  try {
    // 不 require('lunar-typescript/package.json')：该包的 exports 未暴露 ./package.json。
    const pkgPath = require('path').join(require('path').dirname(require.resolve('lunar-typescript')), '..', 'package.json');
    version = JSON.parse(require('fs').readFileSync(pkgPath, 'utf8')).version || 'unknown';
  } catch (e) { /* 保持 unknown */ }

  const leapMonths = [];
  const warnings = [];
  for (let y = from; y <= to; y += 1) {
    let m = 0;
    try {
      m = Number(LunarYear.fromYear(y).getLeapMonth()) || 0;
    } catch (e) {
      warnings.push(`${y} 年查闰月失败：${String(e && e.message || e)}`);
      continue;
    }
    if (!m) continue;                                   // 0 = 该年无闰月
    const lengthDays = leapMonthLength(Lunar, y, m);
    const midDay = Math.ceil(lengthDays / 2);
    const picks = [];
    for (const day of [1, midDay, lengthDays]) {
      if (picks.some((p) => p.lunarDay === day)) continue;
      try {
        const lunar = Lunar.fromYmd(y, -m, day);        // 负月号 = 闰月
        picks.push({
          lunarDay: day,
          solarDate: lunar.getSolar().toYmd(),
          lunarDate: lunar.toString(),
        });
      } catch (e) {
        warnings.push(`${y} 闰${m}月第 ${day} 天算不出来：${String(e && e.message || e)}`);
      }
    }
    leapMonths.push({ lunarYear: y, leapMonth: m, lengthDays, picks });
  }

  return { ok: true, mode: 'leap_months', engine: 'lunar-typescript', version,
           years: [from, to], leapMonths, warnings };
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => { buf += c; });
    process.stdin.on('end', () => resolve(buf));
    process.stdin.on('error', reject);
  });
}

const pick = (s) => (!s ? null : { name: s.name, brightness: s.brightness || null, mutagen: s.mutagen || null, scope: s.scope || null });
const pickAll = (arr) => (arr || []).map(pick);

function main(req) {
  const missing = requireIztro();
  if (missing) return missing;
  const { astro } = iztro;
  const { date, calendar = 'solar', timeIndex, gender, years = [], fixLeap = true } = req;
  if (!date) return { ok: false, error: 'MISSING_DATE' };
  if (timeIndex === undefined || timeIndex === null) return { ok: false, error: 'MISSING_TIME_INDEX' };
  if (!gender) return { ok: false, error: 'MISSING_GENDER' };

  let a;
  try {
    a = calendar === 'lunar'
      ? astro.astrolabeByLunarDate(date, Number(timeIndex), gender, false, fixLeap, 'zh-CN')
      : astro.astrolabeBySolarDate(date, Number(timeIndex), gender, true, 'zh-CN');
  } catch (e) {
    return { ok: false, error: 'IZTRO_THREW', message: String(e && e.message || e) };
  }

  const palaces = a.palaces.map((p) => ({
    name: p.name,
    stem: p.heavenlyStem,
    branch: p.earthlyBranch,
    isBodyPalace: !!p.isBodyPalace,
    major: pickAll(p.majorStars),
    minor: pickAll(p.minorStars),
    adjective: (p.adjectiveStars || []).map((s) => s.name),
    spirits: {
      suiqian: p.suiqian12 || null,
      jiangqian: p.jiangqian12 || null,
      changsheng: p.changsheng12 || null,
      boshi: p.boshi12 || null,
    },
    decadalRange: p.decadal ? p.decadal.range : null,
  }));

  const decadal = a.decadalList().map((x) => ({
    ageRange: x.ageRange,
    yearRange: x.yearRange,
    ganzhi: x.heavenlyStem + x.earthlyBranch,
    stem: x.heavenlyStem,
    branch: x.earthlyBranch,
    palace: x.palaceName,
    mutagen: [x.mutagen[0], x.mutagen[1], x.mutagen[2], x.mutagen[3]],
  }));

  const horoscope = [];
  for (const y of years) {
    let h;
    try { h = a.horoscope(new Date(`${y}-06-15T12:00:00`)); } catch (e) { continue; }
    const flat = (arr, names) => {
      const out = [];
      (arr || []).forEach((grp, i) => {
        const pn = (names && names[i]) || '';
        for (const s of (Array.isArray(grp) ? grp : [grp])) {
          if (s) out.push({ palace: pn, name: s.name, brightness: s.brightness || null, mutagen: s.mutagen || null, scope: s.scope || null });
        }
      });
      return out;
    };
    horoscope.push({
      year: Number(y),
      nominalAge: h.age ? h.age.nominalAge : null,
      soulLimit: h.age ? { ganzhi: h.age.heavenlyStem + h.age.earthlyBranch, branch: h.age.earthlyBranch } : null,
      decadal: {
        ganzhi: h.decadal.heavenlyStem + h.decadal.earthlyBranch,
        mutagen: [h.decadal.mutagen[0], h.decadal.mutagen[1], h.decadal.mutagen[2], h.decadal.mutagen[3]],
        stars: flat(h.decadal.stars, h.decadal.palaceNames),
      },
      yearly: {
        ganzhi: h.yearly.heavenlyStem + h.yearly.earthlyBranch,
        mutagen: [h.yearly.mutagen[0], h.yearly.mutagen[1], h.yearly.mutagen[2], h.yearly.mutagen[3]],
        stars: flat(h.yearly.stars, h.yearly.palaceNames),
      },
      monthly: h.monthly ? { ganzhi: h.monthly.heavenlyStem + h.monthly.earthlyBranch } : null,
    });
  }

  return {
    ok: true,
    engine: 'iztro',
    version: (() => { try { return require('iztro/package.json').version; } catch (e) { return 'unknown'; } })(),
    astrolabe: {
      solarDate: a.solarDate,
      lunarDate: a.lunarDate,
      chineseDate: a.chineseDate,
      fiveElementsClass: a.fiveElementsClass,
      soul: a.soul,
      body: a.body,
      soulPalaceBranch: a.earthlyBranchOfSoulPalace,
      bodyPalaceBranch: a.earthlyBranchOfBodyPalace,
      sign: a.sign,
      zodiac: a.zodiac,
      time: a.time,
      timeRange: a.timeRange,
      palaces,
      decadal,
      horoscope,
      // 节气四柱（立春分界，八字口径）——仅供对照，紫微斗数不用它取四化
      jieqiPillar: jieqiPillar(date, timeIndex),
    },
  };
}

/**
 * 批量排盘模式：`{ mode: "batch", requests: [ <普通请求>, ... ], out: "<文件路径>" }`。
 *
 * 只服务于**对拍器的吞吐**：一次子进程排多张盘，省掉每张盘 ~0.24s 的 node 启动开销
 * （闰月穷举要排上百张盘，逐张起进程时启动开销比计算本身还大）。
 * 结果逐条与单张调用 `main(req)` **同形同序**（完整 astrolabe，不含任何裁剪），
 * 单条抛错只影响那一条。给了 `out` 就把整个响应写进那个文件、stdout 只留一行简短确认：
 * 96 张盘的完整 JSON 约 800KB，塞进管道既慢又顶到系统管道缓冲，落文件最省事。
 * 不传 mode 或 mode 为其它值时，行为与从前逐字一致。
 */
function runBatch(req) {
  const requests = Array.isArray(req.requests) ? req.requests : null;
  if (!requests) {
    return { ok: false, error: 'BAD_BATCH', message: 'batch 模式需要 requests: [ ... ] 数组' };
  }
  let results;
  try {
    results = requests.map((one, i) => Object.assign({ index: i }, main(one || {})));
  } catch (e) {
    return { ok: false, error: 'ENGINE_THREW', message: String(e && e.stack || e) };
  }
  const payload = { ok: true, mode: 'batch', count: results.length, results };

  if (req.out) {
    try {
      require('fs').writeFileSync(String(req.out), JSON.stringify(payload), 'utf8');
    } catch (e) {
      return { ok: false, error: 'BATCH_WRITE_FAILED', message: String(e && e.message || e) };
    }
    return { ok: true, mode: 'batch', count: results.length, out: String(req.out) };
  }
  return payload;
}

readStdin().then((raw) => {
  let req;
  try { req = JSON.parse(raw || '{}'); } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'BAD_JSON', message: String(e.message) }));
    return;
  }
  let res;
  try {
    // 两条专用路径与排盘主路径互不干扰：mode 不在这两个值上时行为与从前逐字一致。
    if (req.mode === 'leap_months') res = runLeapMonths(req);
    else if (req.mode === 'batch') res = runBatch(req);
    else res = main(req);
  } catch (e) {
    res = { ok: false, error: 'ENGINE_THREW', message: String(e && e.stack || e) };
  }
  process.stdout.write(JSON.stringify(res));
}).catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: 'STDIN_FAIL', message: String(e) }));
});
