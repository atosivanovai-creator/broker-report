var dagfuncs = (window.dashAgGridFunctions = window.dashAgGridFunctions || {});
dagfuncs.rub = function (v, d) {
  if (v === null || v === undefined || v === "" || !isFinite(v)) return "";
  v = Number(v); if (Math.abs(v) < 0.5 * Math.pow(10, -(d || 0))) v = 0;
  return v.toLocaleString("ru-RU", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }).replace("-", "\u2212");
};
dagfuncs.pct = function (v, d) {
  if (v === null || v === undefined || !isFinite(v)) return "";
  v = Number(v); if (Math.abs(v) < 0.5 * Math.pow(10, -d)) v = 0;
  return v.toLocaleString("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d }).replace("-", "\u2212") + "%";
};
dagfuncs.ruDate = function (v) {
  if (!v) return "";
  return v.slice(8, 10) + "." + v.slice(5, 7) + "." + v.slice(0, 4);
};
dagfuncs.fmtv = function (p) {
  var d = p.data || {}, v = p.value;
  if (d.sec) return "";
  if (d.fmt === "text") return v || "";
  if (d.fmt === "pct") return dagfuncs.pct(v, 1);
  if (d.fmt === "rub3") return dagfuncs.rub(v, 3);
  return dagfuncs.rub(v, 0);
};
