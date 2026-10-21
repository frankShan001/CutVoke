import { useCallback, useEffect, useState } from "react";
import { tr, useLocale } from "../i18n";
import {
  activateResourcePack,
  addResourceRegistry,
  downloadResourceRegistryPackage,
  getResourcePackManagerStatus,
  installResourcePack,
  listResourceRegistries,
  refreshResourceRegistries,
  removeResourceRegistry,
  rollbackResourcePack,
  trustResourcePackPublisher,
  type ResourcePackEntry,
  type ResourcePackManagerStatus,
  type ResourceRegistry,
} from "../lib/api";

export function ResourcePackManager({ onBusyChange }: { onBusyChange: (busy: boolean) => void }) {
  useLocale();
  const [packManager, setPackManager] = useState<ResourcePackManagerStatus | null>(null);
  const [packManagerError, setPackManagerError] = useState<string | null>(null);
  const [packManagerMessage, setPackManagerMessage] = useState<string | null>(null);
  const [packManagerBusy, setPackManagerBusy] = useState(false);
  const [packManagerStatusLoading, setPackManagerStatusLoading] = useState(false);
  const [packManagerStatusError, setPackManagerStatusError] = useState<string | null>(null);
  const [trustTarget, setTrustTarget] = useState<ResourcePackEntry | null>(null);
  const [registries, setRegistries] = useState<ResourceRegistry[]>([]);
  const [registryLoading, setRegistryLoading] = useState(false);
  const [registryLoadError, setRegistryLoadError] = useState<string | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [registryUrl, setRegistryUrl] = useState("");
  const [registryTrustTarget, setRegistryTrustTarget] = useState<ResourceRegistry | null>(null);
  const [trustFingerprint, setTrustFingerprint] = useState("");
  const [trustPublicKeyPem, setTrustPublicKeyPem] = useState("");

  const refreshPackManager = useCallback(async () => {
    setPackManagerStatusLoading(true);
    setPackManagerStatusError(null);
    try {
      setPackManager(await getResourcePackManagerStatus());
      setPackManagerError(null);
    } catch (error) {
      setPackManagerStatusError(error instanceof Error ? error.message : tr("资源包状态读取失败"));
    } finally {
      setPackManagerStatusLoading(false);
    }
  }, []);

  const loadRegistries = useCallback(async () => {
    setRegistryLoading(true);
    setRegistryLoadError(null);
    try {
      const result = await listResourceRegistries();
      setRegistries(result.registries);
    } catch (error) {
      setRegistryLoadError(error instanceof Error ? error.message : tr("资源目录读取失败"));
    } finally {
      setRegistryLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshPackManager();
    void loadRegistries();
  }, [refreshPackManager, loadRegistries]);

  useEffect(() => { onBusyChange(packManagerBusy); }, [packManagerBusy, onBusyChange]);

  const importResourcePack = async (file: File | null) => {
    if (!file) return;
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      const installed = await installResourcePack(file);
      await refreshPackManager();
      setPackManagerMessage(tr("已安装 v{0}，可在下方列表中启用。", installed.version));
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : tr("资源包安装失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  const activatePack = async (packId: string, version: string) => {
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      setPackManager(await activateResourcePack(packId, version));
      setPackManagerMessage(tr("资源包已启用；刷新页面后加载新的预设和贴纸目录。"));
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : tr("资源包启用失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  const rollbackPack = async () => {
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      setPackManager(await rollbackResourcePack());
      setPackManagerMessage(tr("已回滚资源包；刷新页面后加载回滚版本的目录。"));
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : tr("资源包回滚失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  const addRegistry = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const url = registryUrl.trim();
    if (!url) return;
    setPackManagerBusy(true);
    setRegistryError(null);
    setPackManagerMessage(null);
    try {
      const added = await addResourceRegistry(url);
      setRegistries((current) => [...current.filter((item) => item.url !== added.url), added]);
      setRegistryUrl("");
      setPackManagerMessage(tr("已添加资源目录「{0}」。", added.displayName || added.url));
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : tr("添加资源目录失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  const refreshRegistries = async (url?: string) => {
    setPackManagerBusy(true);
    setRegistryError(null);
    try {
      const result = await refreshResourceRegistries(url);
      if (url) {
        setRegistries((current) => current.map((item) =>
          item.url === url ? result.registries[0] ?? item : item));
      } else {
        setRegistries(result.registries);
      }
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : tr("刷新资源目录失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  const removeRegistry = async (url: string) => {
    setPackManagerBusy(true);
    setRegistryError(null);
    try {
      const result = await removeResourceRegistry(url);
      setRegistries(result.registries);
      setPackManagerMessage(tr("已移除资源目录；已安装的包不会被删除。"));
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : tr("移除资源目录失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  const downloadRegistryPackage = async (registry: ResourceRegistry, packId: string, version: string) => {
    setPackManagerBusy(true);
    setRegistryError(null);
    setPackManagerMessage(tr("正在下载并校验；如果连接中断，再点一次会从已下载位置续传。"));
    try {
      const result = await downloadResourceRegistryPackage(registry.url, packId, version);
      await Promise.all([refreshPackManager(), loadRegistries()]);
      setPackManagerMessage(tr("已下载并安装「{0}」v{1}；可在已安装列表中手动启用。", result.installed.packId, result.installed.version));
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : tr("下载资源包失败；可以重试并续传。"));
      setPackManagerMessage(null);
    } finally {
      setPackManagerBusy(false);
    }
  };

  const trustRegistry = async () => {
    if (!registryTrustTarget?.publisherId || !trustPublicKeyPem || !trustFingerprint.trim()) return;
    setPackManagerBusy(true);
    setRegistryError(null);
    setPackManagerMessage(null);
    try {
      const status = await trustResourcePackPublisher(
        registryTrustTarget.publisherId, trustPublicKeyPem, trustFingerprint.trim(),
      );
      setPackManager(status);
      setRegistryTrustTarget(null);
      const refreshed = await refreshResourceRegistries();
      setRegistries(refreshed.registries);
      setPackManagerMessage(tr("发布者「{0}」的密钥已核对并加入本机信任列表。", registryTrustTarget.publisherId));
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : tr("发布者密钥验证失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  const beginTrustPublisher = (item: ResourcePackEntry) => {
    setRegistryTrustTarget(null);
    setTrustTarget(item);
    setTrustFingerprint("");
    setTrustPublicKeyPem("");
    setPackManagerError(null);
    setPackManagerMessage(null);
  };

  const beginTrustRegistry = (registry: ResourceRegistry) => {
    setTrustTarget(null);
    setRegistryTrustTarget(registry);
    setTrustFingerprint("");
    setTrustPublicKeyPem("");
    setRegistryError(null);
    setPackManagerMessage(null);
  };

  const trustPublisher = async () => {
    if (!trustTarget || !trustPublicKeyPem || !trustFingerprint.trim()) return;
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      const status = await trustResourcePackPublisher(
        trustTarget.publisherId, trustPublicKeyPem, trustFingerprint.trim(),
      );
      setPackManager(status);
      setTrustTarget(null);
      setPackManagerMessage(tr("发布者「{0}」的密钥已核对并加入本机信任列表。", trustTarget.publisherId));
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : tr("发布者密钥验证失败"));
    } finally {
      setPackManagerBusy(false);
    }
  };

  return (
      <details className="resource-pack-manager" open>
        <summary>{tr("离线资源包管理")}{packManager ? tr(" · 当前 v{0}", packManager.active.version) : ""}</summary>
        <div className="resource-pack-manager__body">
          <label className="resource-pack-manager__import">{tr("安装离线 ZIP")}<input type="file" accept=".zip,application/zip" aria-label={tr("安装离线资源包 ZIP")}
              disabled={packManagerBusy}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0] ?? null;
                void importResourcePack(file);
                event.currentTarget.value = "";
              }} />
          </label>
          {packManagerBusy ? <span role="status">{tr("正在处理资源目录或资源包…")}</span> : null}
          {packManagerError ? <p className="resource-pack-manager__error" role="alert">{packManagerError}</p> : null}
          {packManagerStatusLoading ? <p role="status">{tr("正在读取本机资源包状态…")}</p> : null}
          {packManagerStatusError ? (
            <p className="resource-pack-manager__error" role="alert">
              <span>{tr("资源包状态读取失败：")}{packManagerStatusError}</span>
              <button type="button" className="cv-button cv-button--small"
                disabled={packManagerStatusLoading}
                onClick={() => void refreshPackManager()}>{tr("重试资源包状态")}</button>
            </p>
          ) : null}
          {packManagerMessage ? <p className="resource-pack-manager__message" role="status">{packManagerMessage}</p> : null}
          <section className="resource-registry" aria-label={tr("在线资源目录")}>
            <div className="resource-registry__heading">
              <strong>{tr("在线资源目录")}</strong>
              <button type="button" className="cv-button cv-button--small"
                disabled={packManagerBusy || registryLoading} onClick={() => void refreshRegistries()}>{tr("刷新全部")}</button>
            </div>
            <form className="resource-registry__add" onSubmit={(event) => void addRegistry(event)}>
              <label htmlFor="resource-registry-url">{tr("签名目录 HTTPS 地址")}</label>
              <div>
                <input id="resource-registry-url" type="url" value={registryUrl}
                  onChange={(event) => setRegistryUrl(event.currentTarget.value)}
                  placeholder="https://example.com/resources.json" required />
                <button type="submit" className="cv-button cv-button--small"
                  disabled={packManagerBusy || !registryUrl.trim()}>{tr("添加")}</button>
              </div>
              <small>{tr("目录与资源包都需由发布者签名并校验 SHA-256；开发用本机回环地址也可使用 HTTP。")}</small>
            </form>
    {registryLoading ? <p className="cv-hint" role="status">{tr("正在读取在线资源目录…")}</p> : null}
            {registryLoadError ? (
              <p className="resource-pack-manager__error" role="alert">
                <span>{tr("在线资源目录读取失败：")}{registryLoadError}</span>
                <button type="button" className="cv-button cv-button--small"
                  disabled={registryLoading}
                  onClick={() => void loadRegistries()}>{tr("重试资源目录")}</button>
              </p>
            ) : null}
            {registryError ? <p className="resource-pack-manager__error" role="alert">{tr("在线资源目录操作失败：")}{registryError}
            </p> : null}
            {!registryLoading && !registryLoadError && registries.length === 0
              ? <p className="cv-hint">{tr("尚未添加在线资源目录。")}</p> : null}
            <ul className="resource-registry__list" aria-label={tr("在线资源目录列表")}>
              {registries.map((registry) => (
                <li key={registry.url}>
                  <div className="resource-registry__source">
                    <strong>{registry.displayName || registry.registryId || registry.url}</strong>
                    <small>{registry.signatureStatus === "verified"
                      ? tr("目录签名已验证 · {0}", registry.publisherId)
                      : registry.signatureStatus === "unknown_publisher"
                      ? tr("目录发布者未受信任 · {0}", registry.publisherId)
                      : tr("目录暂不可用，保留上次成功读取内容")}</small>
                    {registry.fingerprint ? <code>SHA-256：{registry.fingerprint}</code> : null}
                    {registry.lastError ? <small className="resource-registry__error">{tr("刷新失败：")}{registry.lastError}</small> : null}
                  </div>
                  <div className="resource-registry__actions">
                    {registry.signatureStatus === "unknown_publisher" ? <button type="button"
                      className="cv-button cv-button--small" disabled={packManagerBusy}
                      onClick={() => beginTrustRegistry(registry)}>{tr("核对并信任目录发布者")}</button> : null}
                    <button type="button" className="cv-button cv-button--small"
                      disabled={packManagerBusy} onClick={() => void refreshRegistries(registry.url)}>{tr("刷新")}</button>
                    <button type="button" className="cv-button cv-button--small"
                      disabled={packManagerBusy} onClick={() => void removeRegistry(registry.url)}>{tr("移除")}</button>
                  </div>
                  {registryTrustTarget?.url === registry.url ? (
                    <form className="resource-pack-manager__trust" onSubmit={(event) => {
                      event.preventDefault();
                      void trustRegistry();
                    }}>
                      <strong>{tr("核对 ")}{registry.publisherId}{tr(" 的目录签名密钥")}</strong>
                      <span>{tr("从可信渠道确认此 SHA-256 指纹，再导入发布者提供的 Ed25519 公钥。")}</span>
                      <code>{registry.fingerprint || registry.keyId}</code>
                      <label>{tr("可信渠道给出的 SHA-256 指纹")}<input type="text" value={trustFingerprint}
                          onChange={(event) => setTrustFingerprint(event.currentTarget.value)}
                          autoComplete="off" spellCheck={false} required />
                      </label>
                      <label>{tr("Ed25519 公钥 PEM 文件")}<input type="file" accept=".pem,.pub,text/plain" required
                          onChange={(event) => {
                            const file = event.currentTarget.files?.[0];
                            setTrustPublicKeyPem("");
                            if (file) void file.text().then(setTrustPublicKeyPem).catch(() =>
                              setRegistryError(tr("公钥文件读取失败")));
                          }} />
                      </label>
                      <div>
                        <button type="submit" className="cv-button cv-button--small"
                          disabled={packManagerBusy || !trustFingerprint.trim() || !trustPublicKeyPem}>
                          {packManagerBusy ? tr("验证中…") : tr("验证指纹并信任目录发布者")}
                        </button>
                        <button type="button" className="cv-button cv-button--small"
                          onClick={() => setRegistryTrustTarget(null)}>{tr("取消")}</button>
                      </div>
                    </form>
                  ) : null}
                  {(registry.packages?.length ?? 0) > 0 ? (
                    <ul className="resource-registry__packages" aria-label={tr("{0}中的资源包", registry.displayName || "资源目录")}>
                      {registry.packages?.map((item) => (
                        <li key={`${item.packId}:${item.version}`}>
                          <span><strong>{item.name}</strong> v{item.version}
                            <small>{item.packId} · {(item.sizeBytes / (1024 * 1024)).toFixed(1)}{tr(" MB · SHA-256 已登记")}</small>
                          </span>
                          <button type="button" className="cv-button cv-button--small"
                            disabled={packManagerBusy}
                            onClick={() => void downloadRegistryPackage(registry, item.packId, item.version)}>
                            {packManagerBusy ? tr("处理中…") : tr("下载并安装")}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
          {packManager ? <>
            <ul className="resource-pack-manager__list" aria-label={tr("已安装资源包版本")}>
              {packManager.installed.map((item) => (
                <li key={`${item.packId}:${item.version}`}>
                  <span>
                    <strong>{item.builtin ? tr("内置资源") : item.packId}</strong> v{item.version}
                    <small>{item.presetCount}{tr(" 个预设 · ")}{item.stickerCount}{tr(" 个贴纸 · ")}{item.fileCount}{tr(" 个文件")}{!item.offlineAvailable ? tr(" · 校验异常") : ""}
                      {item.provenanceCoverage ? <><br /><span title={tr("此处只统计资源清单中是否填写许可证与来源字段，不表示法律审核或授权已核实。")}>{tr("授权元数据：许可证 ")}{item.provenanceCoverage.licenseDeclaredCount}/{item.provenanceCoverage.resourceCount}{tr(" · 来源 ")}{item.provenanceCoverage.sourceDeclaredCount}/{item.provenanceCoverage.resourceCount}{tr(" · 字段待补 ")}{item.provenanceCoverage.incompleteCount}</span></> : null}
                      <br />{item.signatureStatus === "bundled" ? tr("应用内置资源") :
                        item.signatureStatus === "verified" ? tr("发布者已验证 · {0}", item.publisherId) :
                        item.signatureStatus === "unknown_publisher" ? tr("发布者未受信任 · {0}", item.publisherId) : tr("未签名 · 发布者未验证")}
                      {item.signatureStatus === "unknown_publisher" ? <><br />{tr("SHA-256 指纹：")}{item.fingerprint || item.keyId}</> : null}
                    </small>
                  </span>
                  {item.signatureStatus === "unknown_publisher" ? (
                    <button type="button" className="cv-button cv-button--small"
                      disabled={packManagerBusy}
                      onClick={() => beginTrustPublisher(item)}>{tr("核对并信任")}</button>
                  ) : null}
                  <button type="button" className="cv-button cv-button--small"
                    disabled={packManagerBusy || item.active || !item.offlineAvailable}
                    onClick={() => void activatePack(item.packId, item.version)}>
                    {item.active ? tr("当前启用") :
                      item.signatureStatus === "unsigned" || item.signatureStatus === "unknown_publisher" ? tr("启用未验证包") : tr("启用")}
                  </button>
                  {trustTarget?.keyId === item.keyId ? (
                    <form className="resource-pack-manager__trust" onSubmit={(event) => {
                      event.preventDefault();
                      void trustPublisher();
                    }}>
                      <strong>{tr("核对 ")}{item.publisherId}{tr(" 的发布者密钥")}</strong>
                      <span>{tr("先从可信渠道确认该 SHA-256 指纹，再填写并导入发布者提供的 Ed25519 公钥 PEM。")}</span>
                      <code>{item.fingerprint || item.keyId}</code>
                      <label>{tr("可信渠道给出的 SHA-256 指纹")}<input type="text" value={trustFingerprint}
                          onChange={(event) => setTrustFingerprint(event.currentTarget.value)}
                          autoComplete="off" spellCheck={false} required />
                      </label>
                      <label>{tr("Ed25519 公钥 PEM 文件")}<input type="file" accept=".pem,.pub,text/plain" required
                          onChange={(event) => {
                            const file = event.currentTarget.files?.[0];
                            setTrustPublicKeyPem("");
                            if (file) void file.text().then(setTrustPublicKeyPem).catch(() =>
                              setPackManagerError(tr("公钥文件读取失败")));
                          }} />
                      </label>
                      <div>
                        <button type="submit" className="cv-button cv-button--small"
                          disabled={packManagerBusy || !trustFingerprint.trim() || !trustPublicKeyPem}>
                          {packManagerBusy ? tr("验证中…") : tr("验证指纹并信任")}
                        </button>
                        <button type="button" className="cv-button cv-button--small"
                          disabled={packManagerBusy}
                          onClick={() => setTrustTarget(null)}>{tr("取消")}</button>
                      </div>
                    </form>
                  ) : null}
                </li>
              ))}
            </ul>
            <button type="button" className="cv-button cv-button--small"
              disabled={packManagerBusy || !packManager.canRollback}
              onClick={() => void rollbackPack()}>{tr("回滚上一版本")}</button>
          </> : null}
        </div>
      </details>
  );
}
