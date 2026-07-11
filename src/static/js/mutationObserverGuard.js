/* Firefox (Gecko) sometimes leaks native anonymous content — find-in-page
   highlights, form-control internals under responsive/mobile emulation (e.g.
   dragging an <input type="range">) — into MutationObserver records. Any
   property access on those nodes throws "Permission denied to access
   property", which crashes Alpine's global observer. Wrap MutationObserver
   and drop inaccessible records before any library sees them.
   Must load BEFORE alpine.js (and without defer). */
(function () {
  'use strict';

  var Native = window.MutationObserver;
  if (!Native) {
    return;
  }

  function accessible(node) {
    try {
      void node.nodeType;
      return true;
    } catch (e) {
      return false;
    }
  }

  function listAccessible(list) {
    for (var i = 0; i < list.length; i += 1) {
      if (!accessible(list[i])) {
        return false;
      }
    }
    return true;
  }

  function GuardedMutationObserver(callback) {
    return new Native(function (records, observer) {
      var safe = [];
      for (var i = 0; i < records.length; i += 1) {
        var r = records[i];
        if (accessible(r.target) && listAccessible(r.addedNodes) && listAccessible(r.removedNodes)) {
          safe.push(r);
        }
      }
      if (safe.length) {
        callback(safe, observer);
      }
    });
  }

  GuardedMutationObserver.prototype = Native.prototype;
  window.MutationObserver = GuardedMutationObserver;
})();
