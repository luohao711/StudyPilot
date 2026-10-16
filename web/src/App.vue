<template>
  <div class="page">
    <header class="topbar">
      <span class="brand">StudyPilot</span>
      <nav class="nav">
        <router-link to="/">首页</router-link>
        <router-link to="/plan">学习计划</router-link>
        <router-link to="/progress">学习进度</router-link>
        <router-link to="/ask">资料问答</router-link>
      </nav>
      <span class="user">罗心月</span>
    </header>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup></script>
