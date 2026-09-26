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
          <small>Local Business Agent</small>
        </span>
      </RouterLink>
      <nav class="topnav">
        <RouterLink to="/">临时任务</RouterLink>
        <RouterLink to="/schedules">定期任务</RouterLink>
        <RouterLink to="/monitor">常驻监控</RouterLink>
        <RouterLink to="/calibrations">监控标定</RouterLink>
      </nav>
      <ActivityPanel />
    </header>
    <main class="page-shell">
      <RouterView :key="$route.fullPath" />
    </main>
  </div>
</template>
