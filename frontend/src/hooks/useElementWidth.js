import { useCallback, useEffect, useState } from 'react'

export default function useElementWidth() {
  const [element, setElement] = useState(null)
  const [width, setWidth] = useState(0)
  const elementRef = useCallback((node) => {
    setElement(node)
  }, [])

  useEffect(() => {
    if (!element) return undefined

    const updateWidth = () => {
      setWidth(element.getBoundingClientRect().width)
    }

    updateWidth()
    const observer = new ResizeObserver(updateWidth)
    observer.observe(element)
    return () => observer.disconnect()
  }, [element])

  return [elementRef, width]
}
