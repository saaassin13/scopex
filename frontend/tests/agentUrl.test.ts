import { test } from 'node:test'
import assert from 'node:assert/strict'
import { agentUrl } from '../src/agentUrl.ts'

test('VPN 直连仍使用根路径，hash 和页面查询不污染请求', () => {
  assert.equal(agentUrl('/tasks?limit=2', 'http://10.200.0.7:8787/?source=cloud#/tasks/one'), '/tasks?limit=2')
})
test('每台设备的接口、图片和下载保留各自路径前缀', () => {
  for (const id of ['device-a', 'device-b']) {
    const prefix = `/api/edge-agents/${id}/proxy/`
    for (const path of ['/tasks/one/images/0', '/tasks/one/export', '/schedules', '/tasks?q=a%2Fb']) {
      assert.equal(agentUrl(path, `https://cloud.example${prefix}#/tasks/one`), prefix + path.slice(1))
    }
  }
})
