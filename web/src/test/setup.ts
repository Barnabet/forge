import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// globals:false means @testing-library/react does not auto-register cleanup,
// so renders would otherwise accumulate in the DOM across tests.
afterEach(cleanup)
