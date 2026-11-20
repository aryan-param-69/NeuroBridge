import { createTheme, type MantineColorsTuple } from '@mantine/core'

// 10-step ramp around the clinical blue #1f5fae (index 6 is the main shade).
const clinical: MantineColorsTuple = [
  '#e8f0fa', '#d3e1f3', '#a9c4e8', '#7da6dc', '#5a8dd2',
  '#3f7bc6', '#1f5fae', '#1a5198', '#144281', '#0e336a',
]

export const theme = createTheme({
  primaryColor: 'clinical',
  colors: { clinical },
  fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif",
  defaultRadius: 'md',
  headings: { fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif", fontWeight: '600' },
})
