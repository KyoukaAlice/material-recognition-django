/* ==========================================================================
   材料识别页的交互
   1) 本地图片预览
   2) 浏览器摄像头取景 + 抓拍（替代原 PyQt5 的 cv2.VideoCapture + QTimer）
   3) 三种图片来源互斥，避免同时提交
   ========================================================================== */
(function () {
  "use strict";

  var video = document.getElementById("camera-video");
  var placeholder = document.getElementById("camera-placeholder");
  var preview = document.getElementById("camera-preview");
  var statusEl = document.getElementById("camera-status");

  var btnOpen = document.getElementById("btn-camera-open");
  var btnShot = document.getElementById("btn-camera-shot");
  var btnClose = document.getElementById("btn-camera-close");

  var fileInput = document.getElementById("id_image");
  var captureInput = document.getElementById("id_capture");
  var sampleSelect = document.getElementById("id_sample");
  var form = document.getElementById("identify-form");

  if (!form) { return; }

  var stream = null;

  function setStatus(text) {
    if (statusEl) { statusEl.textContent = text; }
  }

  function showElement(el, visible) {
    if (!el) { return; }
    if (visible) { el.removeAttribute("hidden"); } else { el.setAttribute("hidden", "hidden"); }
  }

  function showPreview(src) {
    if (placeholder) { placeholder.hidden = true; }
    if (video) { video.hidden = true; }
    if (preview) {
      preview.src = src;
      preview.hidden = false;
    }
  }

  function clearFileInput() {
    if (fileInput) { fileInput.value = ""; }
  }

  function clearCapture() {
    if (captureInput) { captureInput.value = ""; }
  }

  function clearSample() {
    if (sampleSelect) { sampleSelect.value = ""; }
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      stream = null;
    }
    showElement(video, false);
    if (btnOpen) { btnOpen.disabled = false; }
    if (btnShot) { btnShot.disabled = true; }
    if (btnClose) { btnClose.disabled = true; }
  }

  /* ---------- 1. 上传本地图片 ---------- */
  if (fileInput) {
    fileInput.addEventListener("change", function () {
      var file = fileInput.files && fileInput.files[0];
      if (!file) { return; }
      clearCapture();
      clearSample();
      if (preview) {
        preview.src = URL.createObjectURL(file);
        showPreview(preview.src);
      }
      setStatus("已选择本地图片：" + file.name);
    });
  }

  /* ---------- 2. 摄像头 ---------- */
  if (btnOpen) {
    btnOpen.addEventListener("click", function () {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setStatus("当前浏览器不支持摄像头接口，请改用“上传本地图片”。");
        return;
      }
      setStatus("正在请求摄像头权限…");
      navigator.mediaDevices
        .getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false })
        .then(function (mediaStream) {
          stream = mediaStream;
          if (placeholder) { placeholder.hidden = true; }
          if (preview) { preview.hidden = true; }
          video.srcObject = mediaStream;
          video.play();
          showElement(video, true);
          btnOpen.disabled = true;
          btnShot.disabled = false;
          btnClose.disabled = false;
          setStatus("摄像头已开启，点击“拍摄照片”抓取当前画面。");
        })
        .catch(function (err) {
          setStatus("无法打开摄像头：" + err.message + "（请检查权限，或改用上传图片）");
        });
    });
  }

  if (btnShot) {
    btnShot.addEventListener("click", function () {
      if (!stream || !video || !video.videoWidth) {
        setStatus("摄像头尚未就绪，请稍候再试。");
        return;
      }
      var canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

      // 走 JPEG 压缩，避免 dataURL 过大触发上传体积限制
      var dataUrl = canvas.toDataURL("image/jpeg", 0.9);
      clearFileInput();
      clearSample();
      if (captureInput) { captureInput.value = dataUrl; }
      showPreview(dataUrl);
      setStatus("已抓拍当前画面（" + canvas.width + "×" + canvas.height + "），可以点击“识别材料”。");
      stopCamera();
    });
  }

  if (btnClose) {
    btnClose.addEventListener("click", function () {
      stopCamera();
      if (placeholder) { placeholder.hidden = false; }
      if (preview) { preview.hidden = true; preview.removeAttribute("src"); }
      setStatus("摄像头已关闭。");
    });
  }

  /* ---------- 3. 示例图片 ---------- */
  if (sampleSelect) {
    sampleSelect.addEventListener("change", function () {
      if (!sampleSelect.value) { return; }
      clearFileInput();
      clearCapture();
      if (preview) { preview.hidden = true; preview.removeAttribute("src"); }
      if (placeholder) { placeholder.hidden = false; }
      setStatus("已选择示例图片：" + sampleSelect.value);
    });
  }

  /* ---------- 提交防重复 ---------- */
  form.addEventListener("submit", function () {
    var submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "识别中…";
    }
    stopCamera();
  });

  window.addEventListener("beforeunload", stopCamera);
})();
