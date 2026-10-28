/* Отчёт брокера: колонки таблиц запоминаются в этом браузере — порядок, ширина, скрытие, закрепление
   (сортировка и фильтры не запоминаются). Ключ — на таблицу, общий для окон всех счетов: окна
   отдаются с одного адреса, значит и хранилище браузера у них одно.
   Пока человек ничего не менял — «По инструментам» и «По дням» тянутся на ширину экрана, как было
   (раньше это делал columnSize="responsiveSizeToFit", он же и затирал ручные ширины при каждом обновлении).
   Запоминаются только действия человека (источник события ui…), а не подгонка ширины кодом. Только вид. */
(function () {
  var GRIDS = {
    "g-sum": { key: "brep.cols.sum.v1", order: false, fit: false },  // колонки лет не переставляются
    "g-i": { key: "brep.cols.ins.v1", order: true, fit: true },
    "g-d": { key: "brep.cols.day.v1", order: true, fit: true },
    "g-sec": { key: "brep.cols.sec.v1", order: false, fit: true }   // группы колонок не переставляются
  };
  var KEEP = ["colId", "width", "flex", "hide", "pinned"];

  function load(k) {
    try { var v = JSON.parse(localStorage.getItem(k) || "null"); return Array.isArray(v) && v.length ? v : null; }
    catch (e) { return null; }
  }
  function strip(st) {
    return st.map(function (c) { var o = {}; KEEP.forEach(function (f) { if (f in c) o[f] = c[f]; }); return o; });
  }
  function byUser(e) { return /^(ui|contextMenu|toolPanel|autosizeColumns)/.test((e && e.source) || ""); }

  function hook(id, cfg) {
    dash_ag_grid.getApiAsync(id).then(function (a) {
      if (a.__brepCols) return;              // слушатели — один раз
      a.__brepCols = true;
      var busy = false;
      function apply() {
        var st = load(cfg.key);
        if (!st) return false;
        busy = true;
        try { a.applyColumnState({ state: st, applyOrder: cfg.order }); } catch (e) {}
        busy = false;
        return true;
      }
      function fit() { if (cfg.fit && !load(cfg.key)) { try { a.sizeColumnsToFit(); } catch (e) {} } }
      function save() {
        if (busy) return;
        var cur = strip(a.getColumnState()), seen = {};
        cur.forEach(function (c) { seen[c.colId] = 1; });
        (load(cfg.key) || []).forEach(function (c) { if (!seen[c.colId]) cur.push(c); }); // годы вне периода не теряем
        try { localStorage.setItem(cfg.key, JSON.stringify(cur)); } catch (e) {}
      }
      ["columnResized", "columnMoved", "columnVisible", "columnPinned"].forEach(function (ev) {
        a.addEventListener(ev, function (e) {
          if (!byUser(e) || (ev === "columnResized" && e.finished === false)) return;
          save();
        });
      });
      function restore() { apply() || fit(); }
      a.addEventListener("newColumnsLoaded", restore);   // «Сводка»: колонки лет приходят заново со сменой периода
      a.addEventListener("firstDataRendered", restore);
      a.addEventListener("gridSizeChanged", fit);
      restore();
    }).catch(function () {});
  }

  function start() {
    if (!window.dash_ag_grid || !window.dash_ag_grid.getApiAsync) { setTimeout(start, 300); return; }
    Object.keys(GRIDS).forEach(function (id) { hook(id, GRIDS[id]); });
  }
  start();

  // кнопка «Сбросить колонки» (#cols-reset): забыть сохранённое и вернуть вид по умолчанию без перезагрузки
  window.brepColsReset = function () {
    Object.keys(GRIDS).forEach(function (id) {
      var cfg = GRIDS[id];
      try { localStorage.removeItem(cfg.key); } catch (e) {}
      try {
        var a = dash_ag_grid.getApi(id);
        if (a) { a.resetColumnState(); if (cfg.fit) a.sizeColumnsToFit(); }
      } catch (e) {}
    });
  };
  document.addEventListener("click", function (e) {
    var b = e.target && e.target.closest && e.target.closest("#cols-reset");
    if (b) window.brepColsReset();
  });
})();
