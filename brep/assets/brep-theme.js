/* Тема окна (Графит / Тёплая / Светлая). Если окно встроено iframe-ом — переменные цвета копируются из
   родительского окна; иначе тема по выбору на странице «Счета» (localStorage «panel-skin»). */
(function () {
  var VARS = ["--bg", "--card", "--line", "--txt", "--mut", "--acc", "--hdr", "--odd", "--hover", "--okt", "--badt"];
  var last = "";
  function sync() {
    var src = null, light = false;
    try {
      if (window.parent && window.parent !== window && window.parent.document && window.parent.document.body) {
        src = getComputedStyle(window.parent.document.body);
        light = window.parent.document.documentElement.classList.contains("th-light");
      }
    } catch (e) { return; }
    if (!src) { top_(); return; }
    var css = [], key = String(light);
    VARS.forEach(function (v) { var x = src.getPropertyValue(v).trim(); if (x) { css.push(v + ":" + x); key += x; } });
    if (key === last) return;
    last = key;
    var st = document.getElementById("brep-theme-vars");
    if (!st) { st = document.createElement("style"); st.id = "brep-theme-vars"; document.head.appendChild(st); }
    st.textContent = ":root{" + css.join(";") + "}";
    document.documentElement.setAttribute("data-mode", light ? "light" : "dark");
  }
  // окно «Графики» открыто отдельной вкладкой браузера — родителя нет: тема по выбору на странице «Счета» (localStorage «panel-skin»),
  // значения — из assets_grid/zzzzz-themes.css; «Графит» = значения brep.css по умолчанию
  var SKIN = {
    warm: "--bg:#14100c;--card:#1c1712;--hdr:#231c15;--odd:#19140f;--hover:#2a2118;--txt:#efe6da;--mut:#a69683;--acc:#eda04a;--line:#30261c",
    light: "--bg:#f2f4f7;--card:#ffffff;--hdr:#f6f8fb;--odd:#fafbfc;--hover:#eef3ff;--txt:#1d2433;--mut:#5f6b7f;--acc:#2563eb;--line:#dfe4ec;--okt:#18794e;--badt:#c0262d"
  };
  function top_() {
    var k = "graphite";
    try { k = localStorage.getItem("panel-skin") || "graphite"; } catch (e) {}
    if (k === last) return;
    last = k;
    var st = document.getElementById("brep-theme-vars");
    if (!st) { st = document.createElement("style"); st.id = "brep-theme-vars"; document.head.appendChild(st); }
    st.textContent = SKIN[k] ? ":root{" + SKIN[k] + "}" : "";
    document.documentElement.setAttribute("data-mode", k === "light" ? "light" : "dark");
    document.documentElement.setAttribute("data-skin", SKIN[k] ? k : "graphite");
  }
  sync();
  setInterval(sync, 800);
})();
