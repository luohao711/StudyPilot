import { createRouter, createWebHashHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import PlanView from '../views/PlanView.vue'
import ProgressView from '../views/ProgressView.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/plan', name: 'plan', component: PlanView },
  { path: '/progress', name: 'progress', component: ProgressView }
]

export default createRouter({ history: createWebHashHistory(), routes })
