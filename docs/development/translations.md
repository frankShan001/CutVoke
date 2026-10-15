# Interface translations

**English** · [简体中文](translations.zh-CN.md)

CutVoke ships interface languages `zh-CN`, `zh-TW`, `en`, `ja`, `ko`, `es`, `fr`, `de`, `pt-BR`, `ru` and `ar` in the Web editor and its owned desktop shell. Language selection changes labels, help, menus, validation and operation prompts. It never rewrites project names, caption text, media filenames, IDs, command values or serialized projects.

## Current review status

The English catalog was edited for the existing behavior. Traditional Chinese started from OpenCC conversion, with project terminology adjusted. Other catalogs started from an offline M2M-100 translation draft, with high-traffic controls and terminology corrected. The model and its tooling are not shipped or required by CutVoke. Draft generation ran locally; source strings were not sent to a translation service. The public [model card](https://huggingface.co/jncraton/m2m100_418M-ct2-int8) identifies its MIT license and upstream model.

**No locale has received a native-speaker review in this change.** In particular, long explanations may still need idiomatic wording. Key coverage and browser checks establish technical behavior, not linguistic accuracy. Please identify your locale and the exact screen when suggesting corrections; do not mark a locale reviewed without a real reviewer and stated scope.

## Add or correct a message

Catalogs live in [`web/app/src/i18n/locales/`](../../web/app/src/i18n/locales/). Authored Simplified Chinese text acts as the message ID:

```tsx
tr("已导入 {0} 条字幕", count)
```

The English entry is `"Imported {0} captions"`. Keep every numbered placeholder exactly once for each occurrence in the source. Reorder placeholders when grammar requires it. Pass user data as values; **never call `tr(project.name)` or `tr(caption.text)`**. Use complete sentences rather than concatenating translated fragments when adding new copy.

After editing source labels, run from `web/app`:

```bash
npm run i18n:extract
npm run i18n:check
npm run test:i18n
npm run typecheck
npm run build
```

Extraction updates `zh-CN.json` from literal `tr()` and `msg()` calls. Add matching real translations to every other catalog, remove obsolete keys, and rerun checks. Never copy English into missing entries and describe that as language support. Product names, IDs, formulas and shared technical terms can legitimately stay unchanged.

Use `msg(source, ...values)` for stored status text and `formatMessage(message, locale)` when displaying it, so an existing status follows language changes. Keep these descriptors in UI state only. Raw user text must remain a string and must never be looked up in the catalog.

Use `useLocale()` in components that display translations, including memoized components. Translated module-level UI tables use getters so their labels update after a switch. A memo that produces localized text must depend on the locale. Keep locale changes out of effects that save project data or reset user drafts.

## Matching, persistence and fallback

`src/i18n/locale.ts` checks `navigator.languages` in order. Chinese Hant/TW/HK/MO variants select Traditional Chinese; other Chinese variants select Simplified Chinese. Portuguese variants select `pt-BR`; supported base languages match regional tags. Unsupported preferences fall back to English.

The selector has a follow-system option. Web preferences use `localStorage["cutvoke.ui.language"]`; restricted storage still permits a session choice. Storage events synchronize tabs. The root `lang` and `dir` attributes follow the effective language. A missing interface message falls back to English, then authored source text, while interpolated data stays untouched.

The desktop shell saves only `{preference}` in `ui-language.json` under Electron's user-data directory. Its preload supplies this preference before the Web editor starts, so a new local service port does not lose it. Follow-system uses Electron's preferred system languages. Locale IPC is restricted to the owned main frame; setting a preference also checks the local loading-page/service origin. These settings are separate from project data.

Desktop messages live in [`desktop/locales/`](../../desktop/locales/) and are loaded by [`desktop/i18n.cjs`](../../desktop/i18n.cjs). Keep all language keys and numbered placeholders consistent. The loading page, cache-folder title/confirm label, startup errors and service-exit summaries use these catalogs. The app currently has no native application menu; Web toolbar/context menus use the Web catalogs. File lists, OS-provided cancel buttons and other system dialog controls follow the operating system language.

## Effects and resources

Effect requests include the interface `lang`. Existing registry `name` and `description` maps supply metadata for that locale, falling back to English. Add the same locale codes to contribution manifests; the [Soft Reveal example](../../effects/transitions/cutvoke.transition.community.soft-reveal/manifest.json) contains all supported languages. Restart the service after changing manifests because the registry is cached per process.

Keep `effectId`, parameter names, enum values, category and subcategory keys stable. Known category keys have separate display labels. Unknown contributor labels and user presets are shown as provided. Existing resource metadata without a translation may therefore appear in English; raw diagnostic details also remain available for troubleshooting. The interface language does not set speech-recognition language or translate recognized speech.

## Browser review

Keep these content classes separate:

- **Owned interface:** controls, loading, validation, operation summaries and ASR/person progress phases are translated. `backendMessages.ts` enumerates authored phases, including audio extraction, model loading, GPU fallback and parameterized SAM2 tracking. A test checks fixed phase strings against the Python source.
- **Raw diagnostics:** third-party library errors, unknown service details and detailed project-validation output retain their original text. Show a localized summary or original-diagnostic label around them; do not pass free-form values through the translation dictionary. Preflight summaries use stable issue codes, with original details still available.
- **Author metadata:** preset/sticker/template names, descriptions, custom categories and registry parameter descriptions use the language entries supplied by the author, including bundled resources. Missing entries use the existing registry fallback. This does not translate every external resource.

User-authored content deliberately stays unchanged. Browser acceptance uses Windows Chrome. Desktop checks use the actual Windows Electron runtime with isolated profiles and the current built Web client. QA captures use software offscreen rendering; dialog options are intercepted for automated verification, so this is not a visual review of OS dialog chrome, a packaged-installer test, or cross-platform/native-speaker acceptance.

Arabic sets the interface to RTL. The side panels follow reading order; the timeline, timecode, numeric controls, media coordinates and playback direction keep their physical LTR semantics. Check layout and keyboard order, not only translated words. Verify mixed Arabic/Latin user content without changing it.

```bash
npm run test:e2e -- e2e/i18n.spec.ts
```

From `desktop/`, run `npm run test:i18n` for all shell catalogs, placeholder checks, persistence and native-dialog options. On Windows with desktop dependencies and the repository Python environment installed, `npm run test:i18n:electron` runs the real shell. It checks loading in German/Arabic, all locale choices, restart persistence on a new origin, rejected foreign IPC, an actual startup failure and an actual owned-service exit. It uses temporary project/configuration directories; it does not open personal projects. QA-only rendering/runtime flags do not change normal packaged startup.

The suite creates isolated test projects. It covers system matching and reload preference, repeated switching across all locales, unchanged project data, and Chinese/English/German/Arabic menus, dialogs, draft retention and overflow. Add focused cases for new workflows. For manual review, also inspect a narrow viewport, validation and errors, long descriptions, open dialogs during switching, and resource metadata fallback. Do not use private projects or upload screenshots without authorization.
