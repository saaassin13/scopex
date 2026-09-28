<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import ActivityPanel from './components/ActivityPanel.vue'
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/">
        <span class="brand-mark">SX</span>
        <span>
          <strong>ScopeX</strong>
          <small>设备运行检查</small>
        </span>
      </RouterLink>
      <nav class="topnav">
        <RouterLink to="/">任务与结果</RouterLink>
        <RouterLink to="/schedules">定时检查</RouterLink>
        <details class="legacy-navigation"><summary>历史监控</summary><div>
          <RouterLink to="/monitor">监控记录与控制</RouterLink>
          <RouterLink to="/calibrations">历史标定</RouterLink>
        </div></details>
      </nav>
      <ActivityPanel />
    </header>
    <main class="page-shell">
      <RouterView :key="$route.fullPath" />
    </main>
  </div>
</template>
