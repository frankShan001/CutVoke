const language = window.cutvokeDesktop?.initialLanguage;
if (language) {
  document.documentElement.lang = language.locale;
  document.documentElement.dir = language.locale === "ar" ? "rtl" : "ltr";
  document.title = language.messages.loadingTitle;
  for (const node of document.querySelectorAll("[data-message]")) node.textContent = language.messages[node.dataset.message];
  document.querySelector("[role=progressbar]").setAttribute("aria-label", language.messages.loadingProgress);
}
