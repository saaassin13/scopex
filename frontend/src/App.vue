<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/">
        <span class="brand-mark">SX</span>
        <span>
          <strong>ScopeX</strong>
          <small>Local Diagnostic Agent</small>
        </span>
      </RouterLink>
      <div class="topbar-meta">
        <span class="status-dot"></span>
        Local Runtime
      </div>
    </header>
    <main class="page-shell">
      <RouterView />
    </main>
  </div>
</template>
