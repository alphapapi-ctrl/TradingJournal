// Minimal Streamlit custom-component protocol shim (replaces the npm
// streamlit-component-lib). Speaks the postMessage protocol directly.
(function () {
  "use strict";

  const Streamlit = {
    _renderHandler: null,

    setComponentReady: function () {
      window.parent.postMessage(
        { isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1 },
        "*"
      );
    },

    setFrameHeight: function (height) {
      window.parent.postMessage(
        { isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: height },
        "*"
      );
    },

    setComponentValue: function (value) {
      window.parent.postMessage(
        { isStreamlitMessage: true, type: "streamlit:setComponentValue", dataType: "json", value: value },
        "*"
      );
    },

    onRender: function (handler) {
      Streamlit._renderHandler = handler;
    },
  };

  window.addEventListener("message", function (event) {
    const data = event.data;
    if (!data || data.type !== "streamlit:render") return;
    if (Streamlit._renderHandler) {
      Streamlit._renderHandler(data.args || {});
    }
  });

  window.Streamlit = Streamlit;
})();
