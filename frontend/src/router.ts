import { createRouter, createWebHashHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import SchedulesView from './views/SchedulesView.vue'
import TaskView from './views/TaskView.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/schedules', name: 'schedules', component: SchedulesView },
    { path: '/tasks/:id', name: 'task', component: TaskView },
  ],
})
