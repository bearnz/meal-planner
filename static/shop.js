/* Remembering which lines have been ticked off.
 *
 * The shopping list is the one page read standing in a shop, on a phone, where
 * the screen locks every thirty seconds and coming back can reload it. A tick
 * that does not survive that is not a tick, it is a decoration -- which is what
 * the checkboxes were before this file existed.
 *
 * localStorage rather than the server: a tick is about this trip and this
 * phone, it is worthless the moment the plan changes, and posting it would put
 * a round trip between touching a box and seeing it move.
 *
 * Scoped to the plan it was made against (`data-plan` is the plan's generated
 * timestamp), so re-planning starts a clean list instead of showing last week's
 * ticks against this week's dinners. Old scopes are dropped on the way past.
 */
(function () {
  var plan = document.body.dataset.plan || "none";
  var KEY = "shop-ticks";

  function load() {
    try {
      var all = JSON.parse(localStorage.getItem(KEY) || "{}");
      return all.plan === plan ? all : { plan: plan, ticked: [] };
    } catch (e) {
      return { plan: plan, ticked: [] };
    }
  }

  function save(state) {
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch (e) {
      /* Private browsing, a full quota, storage switched off. The list still
         works; it just forgets. Nothing here is worth an alert. */
    }
  }

  var state = load();
  var ticked = new Set(state.ticked);
  var boxes = document.querySelectorAll("input[data-tick]");

  boxes.forEach(function (box) {
    if (ticked.has(box.dataset.tick)) box.checked = true;
    box.addEventListener("change", function () {
      if (box.checked) ticked.add(box.dataset.tick);
      else ticked.delete(box.dataset.tick);
      /* The two views tick different things -- a night's line in one, a whole
         ingredient in the other -- so a key from a view you are not looking at
         is kept rather than pruned to what is on screen. Switching views and
         switching back would otherwise wipe the ticks you made before. */
      save({ plan: plan, ticked: Array.from(ticked) });
    });
  });

  var clear = document.getElementById("untick");
  if (clear) {
    clear.addEventListener("click", function () {
      ticked.clear();
      boxes.forEach(function (box) { box.checked = false; });
      save({ plan: plan, ticked: [] });
    });
  }
})();
