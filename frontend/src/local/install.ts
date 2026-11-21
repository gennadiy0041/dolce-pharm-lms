// Установка на домашний экран. Chrome на Android сообщает, что приложение можно установить, —
// мы сохраняем это событие и показываем понятную кнопку в профиле. В Safari на iPhone такого события
// нет: там ярлык ставится через «Поделиться» → «На экран “Домой”».
import { createStore, useStore } from './store'

interface InstallEvent extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

const store = createStore<{ event: InstallEvent | null }>({ event: null })

export function watchInstall(): void {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    store.set({ event: e as InstallEvent })
  })
  window.addEventListener('appinstalled', () => store.set({ event: null }))
}

export function useCanInstall(): boolean {
  return useStore(store).event !== null
}

export async function promptInstall(): Promise<boolean> {
  const { event } = store.get()
  if (!event) return false
  await event.prompt()
  const choice = await event.userChoice
  store.set({ event: null })
  return choice.outcome === 'accepted'
}
