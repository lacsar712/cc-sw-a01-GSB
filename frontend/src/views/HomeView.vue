<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()
const role = ref(localStorage.getItem('role') || '')
const jobs = ref([])
const lamps = ref([])
const err = ref('')
const ok = ref('')
const form = ref({ lamp_id: null, nominal_nm: 587.56, measured_nm: 587.5 })
let timer

const writer = computed(() => role.value === 'writer')
// 只能圈仍挂牌且圈了可校准的灯
const choices = computed(() => lamps.value.filter((l) => l.active && l.calibratable))

async function refresh() {
  if (!localStorage.getItem('tok')) return
  try {
    const [jobRows, lampData] = await Promise.all([api('/api/jobs'), api('/api/lamps')])
    jobs.value = jobRows
    lamps.value = lampData.active
    err.value = ''
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function submit() {
  err.value = ''
  ok.value = ''
  if (!form.value.lamp_id) {
    err.value = '没圈可校准灯：请先在灯种牌上格建档并圈可校准，再选灯入队'
    return
  }
  try {
    const res = await api('/api/jobs', { method: 'POST', body: JSON.stringify(form.value) })
    ok.value = `已入队 #${res.id}（${res.lamp}），等待处理`
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function goDetail(id) {
  router.push(`/jobs/${id}`)
}

onMounted(() => {
  role.value = localStorage.getItem('role') || ''
  refresh()
  timer = setInterval(refresh, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <p v-if="err" style="color:#b00020">{{ err }}</p>
    <p v-if="ok" style="color:#0a7d2c">{{ ok }}</p>
    <section v-if="writer" style="margin:16px 0; padding:12px; border:1px solid #ccc;">
      <h3>提交校准（绑灯种牌）</h3>
      <label>
        圈选灯种
        <select v-model="form.lamp_id">
          <option :value="null" disabled>— 请选择仍可校准的挂牌灯 —</option>
          <option v-for="l in choices" :key="l.id" :value="l.id">{{ l.name }}</option>
        </select>
      </label>
      <label>标称 nm <input type="number" step="0.01" v-model.number="form.nominal_nm" /></label>
      <label>实测 nm <input type="number" step="0.01" v-model.number="form.measured_nm" /></label>
      <button @click="submit">入队</button>
      <p v-if="!choices.length" class="hint">
        没有可圈选的灯：请先到顶栏「灯种牌」建档并圈可校准。摘牌或未圈可校准的灯不能提交。
      </p>
    </section>
    <table border="1" cellpadding="6" style="border-collapse:collapse; width:100%;">
      <thead>
        <tr>
          <th>编号</th><th>灯种（冻结称呼）</th><th>标称</th><th>实测</th><th>状态</th><th>结论</th><th>理由</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="j in jobs"
          :key="j.id"
          style="cursor:pointer"
          @click="goDetail(j.id)"
        >
          <td>{{ j.id }}</td>
          <td>{{ j.lamp }}</td>
          <td>{{ j.nominal_nm }}</td>
          <td>{{ j.measured_nm }}</td>
          <td>{{ j.status }}</td>
          <td>{{ j.verdict }}</td>
          <td>{{ j.reason }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
