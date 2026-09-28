/** 页面所在目录就是 API 前缀；hash 路由不会改变它，设备之间不共享前缀。 */
export function agentUrl(path: string, pageUrl = window.location.href): string {
  const base = new URL(pageUrl)
  base.search = ''
  base.hash = ''
  if (!base.pathname.endsWith('/')) base.pathname += '/'
  const target = new URL(path.replace(/^\//, ''), base)
  return target.pathname + target.search
}
