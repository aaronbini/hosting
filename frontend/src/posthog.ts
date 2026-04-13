import posthog from 'posthog-js'

const key = import.meta.env.VITE_POSTHOG_KEY
const host = import.meta.env.VITE_POSTHOG_HOST

if (key) {
  posthog.init(key, { api_host: host || 'https://us.i.posthog.com' })
}

export default posthog
