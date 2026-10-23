var sh = new ActiveXObject('Shell.Application');
var wins = sh.Windows();
for (var i = 0; i < wins.Count; i++) {
  try {
    var w = wins.Item(i);
    var sel = w.Document.SelectedItems();
    if (sel && sel.Count > 0) {
      for (var j = 0; j < sel.Count; j++) {
        WScript.Echo(sel.Item(j).Path);
      }
      break;
    }
  } catch(e) {}
}
